"""The CAL FIRE Incidents integration.

This file is Home Assistant's required entry point for a custom integration.
It's responsible for two things:

1. Defining a "coordinator" — an object that knows how to fetch CAL FIRE's
   incident feed on a timer and hand the parsed result to whatever entities
   need it. Home Assistant's `DataUpdateCoordinator` base class does the
   scheduling/retry/error-handling for us; we just fill in "how do I get
   the data" (`_async_update_data` below).
2. Wiring that coordinator up when the integration is added/removed via the
   UI (`async_setup_entry` / `async_unload_entry`).

The actual Home Assistant *entities* (the things that show up as
sensor.whatever in your dashboard) live in sensor.py, not here.
"""
from __future__ import annotations

from datetime import timedelta
import logging
import math

import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_URL,
    CONF_RADIUS_KM,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MISSING_POLLS_BEFORE_REMOVAL,
)

# Home Assistant convention: every integration gets its own logger named
# after its Python module, e.g. "custom_components.calfire". Users can then
# turn on debug logging for just this integration in configuration.yaml.
_LOGGER = logging.getLogger(__name__)

# Which entity platforms this integration provides. We only have sensors,
# but if we later added e.g. a binary_sensor, it would be listed here too.
PLATFORMS = ["sensor"]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two lat/lon points, in km.

    This is just standard spherical-earth distance math (the "haversine
    formula") — no CAL FIRE-specific logic here. We use it to work out how
    far a fire is from your Home Assistant instance's configured location,
    so the optional radius filter can decide whether to include it.
    """
    earth_radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def _to_float(value) -> float | None:
    """Coerce a CAL FIRE numeric-ish value into a real Python float.

    CAL FIRE's feed doesn't consistently send plain numbers — some fields
    have shown up as formatted strings like "1,234" (thousands separator)
    or "25%" (percent sign baked into the string). This strips that
    formatting and converts to a float, returning None if the value is
    missing or genuinely not a number.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    # Strip thousands-separator commas and percent signs, then try again.
    cleaned = str(value).replace(",", "").replace("%", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _first(props: dict, *keys):
    """Return the first present, non-None value among several possible keys.

    CAL FIRE's API is internal/undocumented (it's meant for their own
    website, not third parties), so field names aren't guaranteed and have
    been reverse-engineered rather than pulled from an official spec.
    Different snapshots of the API have used different naming for the same
    concept (e.g. `County` vs `CountiesList`, `Started` vs `StartedDate`).
    Rather than hard-coding a single guessed name and silently getting
    `None` back if it's wrong, we try a short list of known variants in
    order and use whichever one actually shows up in the response.
    """
    for key in keys:
        if key in props and props[key] is not None:
            return props[key]
    return None


class CalFireCoordinator(DataUpdateCoordinator):
    """Polls the CAL FIRE feed on a timer and parses it into a plain dict.

    Subclassing `DataUpdateCoordinator` gets us, for free:
      - a background timer that calls `_async_update_data` every
        `update_interval`
      - de-duplication, so multiple entities sharing this coordinator don't
        each make their own HTTP request
      - automatic retry/backoff and marking entities "unavailable" if a
        fetch fails (raising `UpdateFailed` below signals a failed fetch)

    After each successful update, `self.data` holds whatever
    `_async_update_data` returned — in our case, a dict of
    `{unique_id: incident_details_dict}` for every currently-active fire.
    Entities in sensor.py read from `self.data` (via `coordinator.data`).
    """

    def __init__(self, hass: HomeAssistant, radius_km: float, scan_minutes: int) -> None:
        # Optional filter: if > 0, incidents farther than this from home are
        # dropped entirely (never become entities). 0 means "no filtering".
        self.radius_km = radius_km

        # Your Home Assistant instance's configured location (Settings ->
        # System -> General), used as the center point for radius filtering
        # and for each incident's "distance_km" attribute.
        self.home_lat = hass.config.latitude
        self.home_lon = hass.config.longitude

        # Home Assistant provides a shared aiohttp session per-instance so
        # we're not opening a fresh TCP connection for every poll.
        self._session = async_get_clientsession(hass)

        # --- Bookkeeping used to detect "what changed" between polls ---
        # `_known_ids` is the set of incident IDs we saw on the *previous*
        # successful poll. Comparing it to the current poll's IDs is how we
        # figure out which fires are brand new vs. which have disappeared.
        self._known_ids: set[str] = set()

        # For each incident ID that's currently missing from the feed, how
        # many consecutive polls in a row has it been missing? Used to
        # avoid treating a single flaky API response as a fire actually
        # closing out (see MISSING_POLLS_BEFORE_REMOVAL in const.py).
        self._missing_counts: dict[str, int] = {}

        # Whether we've completed at least one successful poll yet. On the
        # very first poll after Home Assistant starts, *every* fire in the
        # feed is technically "new" to us, but they're not newly-started
        # fires — they were already burning. We use this flag to skip
        # "new fire" detection just for that first poll.
        self._first_refresh_done: bool = False

        # Public attributes that sensor.py reads directly (Python doesn't
        # enforce private/public, but the leading underscore above signals
        # "internal to this class" vs. these, which are part of the
        # coordinator's public interface used by the sensor platform).
        self.latest_incident: dict | None = None  # most recently new fire seen
        self.newly_added: dict[str, dict] = {}     # all fires new this poll
        self.removed_ids: set[str] = set()         # fires confirmed gone this poll

        # Hand control to DataUpdateCoordinator's own __init__, which sets
        # up the polling timer using `update_interval`.
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_minutes),
        )

    async def _async_update_data(self):
        """Fetch the feed and return a dict of {unique_id: incident dict}.

        This is the one method `DataUpdateCoordinator` requires us to
        implement — it gets called automatically on the timer. Whatever we
        return becomes `self.data` (and `coordinator.data` as seen from
        sensor.py) until the next successful call.
        """
        # --- Step 1: fetch the raw GeoJSON from CAL FIRE ---
        try:
            async with async_timeout.timeout(30):
                resp = await self._session.get(API_URL)
                resp.raise_for_status()  # raises if HTTP status is 4xx/5xx
                geojson = await resp.json(content_type=None)
        except Exception as err:  # noqa: BLE001 - deliberately broad: any
            # failure here (network error, timeout, bad JSON, HTTP error)
            # should be reported to the coordinator the same way, so it can
            # mark entities unavailable and retry on the next scheduled poll.
            raise UpdateFailed(f"Error fetching CAL FIRE feed: {err}") from err

        # --- Step 2: parse each GeoJSON "feature" (= one fire) into a plain dict ---
        # GeoJSON's structure: a FeatureCollection with a "features" list;
        # each feature has "geometry" (the location) and "properties" (all
        # the actual fire details like name, acres, containment, etc).
        incidents: dict[str, dict] = {}
        logged_sample = False
        for feature in geojson.get("features", []):
            props = feature.get("properties", {}) or {}

            # The first time through this loop, log the raw property names
            # CAL FIRE actually sent, at debug level. This is a diagnostic
            # aid: since the API is undocumented, if a field we expect
            # turns out to be missing or renamed, enabling debug logging
            # for this integration will show exactly what's available.
            if not logged_sample:
                _LOGGER.debug("Sample CAL FIRE incident properties: %s", sorted(props.keys()))
                logged_sample = True

            # Prefer explicit Latitude/Longitude properties if present;
            # otherwise fall back to the GeoJSON geometry's coordinates
            # (which are ordered [longitude, latitude], not the more
            # common lat-then-lon order — easy to get backwards).
            geometry = feature.get("geometry", {}) or {}
            coords = geometry.get("coordinates")
            lat = props.get("Latitude")
            lon = props.get("Longitude")
            if (lat is None or lon is None) and coords and len(coords) >= 2:
                lon, lat = coords[0], coords[1]
            lat = _to_float(lat)
            lon = _to_float(lon)

            # Every fire needs a stable ID so Home Assistant can recognize
            # "this is the same fire as last time" across polls. Try a
            # couple of likely ID fields, falling back to the fire's name
            # as a last resort (not ideal — two different fires could in
            # theory share a name — but better than nothing).
            unique_id = str(
                props.get("UniqueId") or props.get("IncidentId") or props.get("Name")
            )
            if not unique_id:
                continue  # can't build an entity without something to key it on

            # Work out how far this fire is from your HA instance's home
            # location, so it can be exposed as an attribute and used for
            # the optional radius filter below.
            distance_km = None
            if lat is not None and lon is not None:
                distance_km = _haversine_km(self.home_lat, self.home_lon, lat, lon)

            # If a radius filter is configured (radius_km > 0) and this
            # fire is farther away than that, skip it entirely — it never
            # becomes an entity.
            if self.radius_km and distance_km is not None and distance_km > self.radius_km:
                continue

            # Pull out all the fields we care about into a plain dict.
            # `_first()` tries several possible field-name variants in
            # order (see its docstring above) since CAL FIRE's exact
            # naming has proven inconsistent across API snapshots.
            incidents[unique_id] = {
                "unique_id": unique_id,
                "name": props.get("Name") or "Unknown Incident",
                "acres_burned": _to_float(_first(props, "AcresBurned", "AcresBurnedDisplay")),
                "percent_contained": _to_float(
                    _first(props, "PercentContained", "PercentContainedDisplay")
                ),
                "county": _first(props, "County", "CountiesList"),
                "admin_unit": props.get("AdminUnit"),
                "incident_type": _first(
                    props, "IncidentType", "Type", "IncidentTypeDisplay"
                ),
                "started": _first(props, "Started", "StartedDate"),
                "updated": _first(props, "Updated", "UpdatedDate"),
                "url": props.get("Url"),
                "is_active": props.get("IsActive"),
                "latitude": lat,
                "longitude": lon,
                "distance_km": round(distance_km, 1) if distance_km is not None else None,
            }

        # --- Step 3: work out which fires are brand new since last poll ---
        # We compare this poll's incident IDs against `_known_ids`, which
        # holds whatever IDs we saw last time. Anything in `incidents` but
        # not in `_known_ids` is new. This powers both the
        # `sensor.calfire_latest_incident` entity and the
        # `calfire_new_incident` event fired from sensor.py.
        if self._first_refresh_done:
            new_ids = set(incidents) - self._known_ids
            self.newly_added = {uid: incidents[uid] for uid in new_ids}
            if new_ids:
                # If more than one fire showed up in the same poll window
                # (e.g. after HA was offline for a while), arbitrarily pick
                # whichever has burned the most acres so far as "the"
                # latest incident — there's no perfect answer here since
                # they're all equally "new" to us.
                newest_id = max(new_ids, key=lambda uid: incidents[uid]["acres_burned"] or 0)
                self.latest_incident = incidents[newest_id]
        else:
            # This is the very first successful poll since HA started, so
            # every currently-active fire looks "new" to us — but they're
            # not newly-started, we just haven't seen them before. Don't
            # fire notifications for the entire existing fire list.
            self._first_refresh_done = True
            self.newly_added = {}

        # --- Step 4: work out which known fires have dropped out of the feed ---
        # A fire disappearing from the feed usually means CAL FIRE marked
        # it contained/closed and stopped listing it. But it could also be
        # a temporary hiccup in their API (an empty or partial response).
        # To avoid deleting an entity for a fire that's still actually
        # burning, we require a fire to be missing for
        # MISSING_POLLS_BEFORE_REMOVAL consecutive polls in a row before
        # we consider it truly gone.
        removed_ids: set[str] = set()
        for uid in self._known_ids:
            if uid in incidents:
                # Still present — reset its "missing streak" back to zero.
                self._missing_counts.pop(uid, None)
                continue
            count = self._missing_counts.get(uid, 0) + 1
            self._missing_counts[uid] = count
            if count >= MISSING_POLLS_BEFORE_REMOVAL:
                removed_ids.add(uid)
        for uid in removed_ids:
            # No longer need to track these — they've been confirmed gone.
            self._missing_counts.pop(uid, None)
        self.removed_ids = removed_ids

        # Update our "known IDs" snapshot for next time: drop anything we
        # just confirmed removed, and add everything present in this poll
        # (fires still within their grace period stay in `_known_ids` even
        # though they're momentarily missing from `incidents`).
        self._known_ids = (self._known_ids - removed_ids) | set(incidents)

        return incidents


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Called by Home Assistant when this integration is added/started.

    `entry` represents one instance of the integration as configured via
    the UI (see config_flow.py) — its `.data` dict holds whatever the user
    submitted in the setup form (radius, scan interval).
    """
    radius_km = entry.data.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM)
    scan_minutes = entry.data.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)

    coordinator = CalFireCoordinator(hass, radius_km, scan_minutes)

    # Do one fetch right now (rather than waiting for the first timer tick)
    # so entities have real data as soon as setup finishes. If this fails,
    # Home Assistant will show the integration as failed to set up and
    # will retry automatically.
    await coordinator.async_config_entry_first_refresh()

    # Stash the coordinator somewhere sensor.py can find it. `hass.data` is
    # the standard Home Assistant "shared state between files" mechanism —
    # think of it as a big dict namespaced by each integration's domain.
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Tell Home Assistant to now set up each platform listed in PLATFORMS
    # (just "sensor" for us) — this is what actually triggers sensor.py's
    # `async_setup_entry` to run and create entities.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Called when the integration is removed or reloaded.

    Cleans up after ourselves: tears down the sensor platform, then drops
    our coordinator reference from `hass.data` so it can be garbage
    collected (and its polling timer stops).
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
