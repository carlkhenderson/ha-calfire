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
import re

import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_registry import (
    async_entries_for_config_entry,
    async_get as async_get_entity_registry,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_URL,
    CONF_CENTER_LATITUDE,
    CONF_CENTER_LONGITUDE,
    CONF_DISTANCE_UNIT,
    CONF_RADIUS_KM,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_DISTANCE_UNIT,
    DEFAULT_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    KM_TO_MILES,
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


# ASP.NET/Umbraco APIs (which CAL FIRE's is built on) sometimes serialize
# dates as `/Date(1626307200000-0700)/` — a legacy JSON.NET format encoding
# milliseconds-since-epoch plus a timezone offset — rather than plain ISO
# 8601. This regex recognizes that format so `_parse_started_date` below
# can handle either.
_DOTNET_DATE_RE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")


def _parse_started_date(value):
    """Best-effort parse of CAL FIRE's "started" date into an aware datetime.

    Returns None if `value` is missing or in a format we don't recognize,
    rather than raising — a fire missing/unparseable a start date shouldn't
    break the whole integration, it should just mean `days_burning` (see
    `_async_update_data`) comes back as None for that fire.
    """
    if not value:
        return None

    match = _DOTNET_DATE_RE.match(str(value))
    if match:
        return dt_util.utc_from_timestamp(int(match.group(1)) / 1000)

    # `dt_util.parse_datetime` (Home Assistant's own date parsing utility,
    # so no extra dependency needed) handles standard ISO 8601 strings,
    # with or without a timezone offset.
    parsed = dt_util.parse_datetime(str(value))
    if parsed is None:
        return None
    # If the string had no timezone info, assume it was already UTC rather
    # than leaving it "naive" — Python raises an error comparing a naive
    # and timezone-aware datetime, and `dt_util.as_utc` is a no-op if it
    # already has a timezone.
    return dt_util.as_utc(parsed)


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

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        hub_name: str,
        radius_km: float,
        scan_minutes: int,
        center_lat: float | None = None,
        center_lon: float | None = None,
        distance_unit: str = DEFAULT_DISTANCE_UNIT,
    ) -> None:
        # Which config entry (integration instance) this coordinator
        # belongs to, and its display name. Having more than one hub set
        # up (e.g. one centered on your home, one on a relative's) is
        # fully supported — these two let per-fire entities be scoped and
        # labeled per hub, so two hubs' fires never collide with the same
        # unique_id even if their radii overlap (see CalFireIncidentSensor
        # in sensor.py), and so a "hub" attribute can be used to filter a
        # dashboard down to just one hub's fires.
        self.entry_id = entry_id
        self.hub_name = hub_name

        # Optional filter: if > 0, incidents farther than this from the
        # center point are dropped entirely (never become entities). 0
        # means "no filtering". Always interpreted in kilometers regardless
        # of `distance_unit` below, to keep its meaning unambiguous.
        self.radius_km = radius_km

        # Which unit the "distance" / "distance_unit" attributes should be
        # shown in (see _async_update_data). Doesn't affect radius_km above,
        # or the always-present "distance_km" attribute — just adds a
        # unit-aware convenience pair for dashboards/automations that would
        # rather not do the km->mi conversion themselves.
        self.distance_unit = distance_unit

        # The center point used for both the radius filter and each
        # incident's "distance_km" attribute. Defaults to Home Assistant's
        # configured home location (Settings -> System -> General) unless
        # the user set an explicit override in the setup/options form.
        self.center_lat = center_lat if center_lat is not None else hass.config.latitude
        self.center_lon = center_lon if center_lon is not None else hass.config.longitude

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

    def seed_known_ids(self, ids: set[str]) -> None:
        """Add IDs that should be treated as 'previously known' fires.

        Called once at startup with fire IDs found already registered as
        entities from a prior Home Assistant session (see
        `async_setup_entry` below). Without this, a fire entity left over
        from before a restart or integration reload — for a fire that's
        since disappeared from the feed — would never be evaluated for
        removal at all: this coordinator would have no record of ever
        having "known" about it, so it could never be recognized as
        missing. It would simply sit in Home Assistant forever, stuck
        `unavailable`.
        """
        self._known_ids |= ids

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

            # Work out how far this fire is from the configured center
            # point, so it can be exposed as an attribute and used for the
            # optional radius filter below.
            distance_km = None
            if lat is not None and lon is not None:
                distance_km = _haversine_km(self.center_lat, self.center_lon, lat, lon)

            # If a radius filter is configured (radius_km > 0) and this
            # fire is farther away than that, skip it entirely — it never
            # becomes an entity.
            if self.radius_km and distance_km is not None and distance_km > self.radius_km:
                continue

            distance_mi = distance_km * KM_TO_MILES if distance_km is not None else None
            # A convenience pair reflecting whichever unit was chosen in
            # setup/options, so dashboards and automations don't have to do
            # the km<->mi conversion (or know which raw attribute to use)
            # themselves. `distance_km` (and the new `distance_mi`) are
            # still always present too, regardless of this preference.
            distance_in_preferred_unit = (
                distance_mi if self.distance_unit == "mi" else distance_km
            )

            raw_started = _first(props, "Started", "StartedDate")
            started_dt = _parse_started_date(raw_started)
            days_burning = None
            if started_dt is not None:
                days_burning = max((dt_util.utcnow() - started_dt).days, 0)
            elif raw_started:
                # We got *something* for the start date but couldn't parse
                # it — surface that in debug logs rather than silently
                # leaving days_burning as None with no explanation.
                _LOGGER.debug("Could not parse started date %r for %s", raw_started, unique_id)

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
                )
                or 0.0,
                "county": _first(props, "County", "CountiesList"),
                "admin_unit": props.get("AdminUnit"),
                "incident_type": _first(
                    props, "IncidentType", "Type", "IncidentTypeDisplay"
                ),
                "started": raw_started,
                "days_burning": days_burning,
                "updated": _first(props, "Updated", "UpdatedDate"),
                "url": props.get("Url"),
                "is_active": props.get("IsActive"),
                "latitude": lat,
                "longitude": lon,
                "distance_km": round(distance_km, 1) if distance_km is not None else None,
                "distance_mi": round(distance_mi, 1) if distance_mi is not None else None,
                "distance": (
                    round(distance_in_preferred_unit, 1)
                    if distance_in_preferred_unit is not None
                    else None
                ),
                "distance_unit": self.distance_unit,
                "hub": self.hub_name,
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
            _LOGGER.debug(
                "Incident %s missing from feed (%s/%s consecutive polls)",
                uid,
                count,
                MISSING_POLLS_BEFORE_REMOVAL,
            )
            if count >= MISSING_POLLS_BEFORE_REMOVAL:
                removed_ids.add(uid)
                _LOGGER.debug("Incident %s confirmed gone; entity will be removed", uid)
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
    the UI (see config_flow.py). Two sources of settings get merged here:
    `entry.data` (whatever was submitted in the initial setup form) and
    `entry.options` (whatever was later changed via the "Configure" gear
    icon / options flow). Options take precedence, since they represent
    the most recent choice.
    """

    def _get(key: str, default):
        return entry.options.get(key, entry.data.get(key, default))

    radius_km = _get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM)
    scan_minutes = _get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)
    center_lat = _get(CONF_CENTER_LATITUDE, None)
    center_lon = _get(CONF_CENTER_LONGITUDE, None)
    distance_unit = _get(CONF_DISTANCE_UNIT, DEFAULT_DISTANCE_UNIT)

    coordinator = CalFireCoordinator(
        hass,
        entry.entry_id,
        entry.title,
        radius_km,
        scan_minutes,
        center_lat,
        center_lon,
        distance_unit,
    )

    # If this integration was previously set up (and is now reloading, or
    # Home Assistant just restarted), there may already be per-fire
    # entities sitting in the registry from before. Find them and seed the
    # coordinator's tracking with their fire IDs *before* the first fetch,
    # so leftover entities for fires that closed out while HA was down get
    # properly evaluated for removal instead of being invisible to that
    # logic forever (see `seed_known_ids`'s docstring for why this matters).
    #
    # Per-fire unique_ids are scoped to this specific config entry
    # (`calfire_<entry_id>_<fire_id>`) so that two hub instances (e.g. one
    # centered on your home, one on a relative's) never collide over the
    # same fire, even if their radii overlap. Versions of this integration
    # before multi-hub support used an unscoped format
    # (`calfire_<fire_id>`) — rather than orphaning those entities, we
    # migrate them to the new format in place here, which preserves their
    # entity_id, history, and any dashboard/automation references.
    latest_incident_suffix = "_latest_incident"
    old_unique_id_prefix = f"{DOMAIN}_"
    new_unique_id_prefix = f"{DOMAIN}_{entry.entry_id}_"
    ent_reg = async_get_entity_registry(hass)
    leftover_fire_ids: set[str] = set()
    for registry_entry in async_entries_for_config_entry(ent_reg, entry.entry_id):
        if registry_entry.domain != "sensor":
            continue
        uid = registry_entry.unique_id
        if uid.endswith(latest_incident_suffix):
            continue  # the singleton entity, already entry-scoped, nothing to migrate
        if uid.startswith(new_unique_id_prefix):
            leftover_fire_ids.add(uid[len(new_unique_id_prefix) :])
        elif uid.startswith(old_unique_id_prefix):
            fire_id = uid[len(old_unique_id_prefix) :]
            ent_reg.async_update_entity(
                registry_entry.entity_id,
                new_unique_id=f"{new_unique_id_prefix}{fire_id}",
            )
            leftover_fire_ids.add(fire_id)
    coordinator.seed_known_ids(leftover_fire_ids)

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

    # If the user later changes settings via the options flow
    # (config_flow.py's CalFireOptionsFlow), `entry.options` changes but
    # nothing else happens automatically — the coordinator above was built
    # with a snapshot of the old settings. Reloading the whole config entry
    # re-runs this function from scratch with the new values, which is the
    # simplest way to make radius/scan-interval/center-point changes take
    # effect immediately rather than requiring a manual HA restart.
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_update))

    return True


async def _async_reload_on_options_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload this config entry whenever its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


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
