"""The CAL FIRE Incidents integration.

Sets up a `CalFireCoordinator` (polls CAL FIRE's feed on a timer) per config
entry. The actual entities live in sensor.py.
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
    CONF_RADIUS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_DISTANCE_UNIT,
    DEFAULT_RADIUS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    KM_TO_MILES,
    MI_TO_KM,
    MISSING_POLLS_BEFORE_REMOVAL,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor"]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in km."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _to_float(value) -> float | None:
    """Parse CAL FIRE's numeric-ish values ("1,234", "25%", 12.3, None) into a float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _first(props: dict, *keys):
    """First present value among several possible field-name variants.

    CAL FIRE's API is undocumented and has used different names for the
    same field across snapshots (e.g. `County` vs `CountiesList`).
    """
    for key in keys:
        if key in props and props[key] is not None:
            return props[key]
    return None


# Legacy ASP.NET/Umbraco date format: /Date(<epoch_ms>[+-]tzoffset)/
_DOTNET_DATE_RE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")


def _parse_started_date(value):
    """Best-effort parse of CAL FIRE's start date. Returns None if unrecognized."""
    if not value:
        return None
    match = _DOTNET_DATE_RE.match(str(value))
    if match:
        return dt_util.utc_from_timestamp(int(match.group(1)) / 1000)
    parsed = dt_util.parse_datetime(str(value))
    return dt_util.as_utc(parsed) if parsed else None


class CalFireCoordinator(DataUpdateCoordinator):
    """Polls the CAL FIRE feed and parses it into {fire_id: incident_dict}.

    Also tracks which fires are new or have dropped out of the feed since
    the last poll, via `newly_added` / `removed_ids` / `latest_incident`,
    which sensor.py uses to create/remove entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        hub_name: str,
        radius: float,
        scan_minutes: int,
        center_lat: float | None = None,
        center_lon: float | None = None,
        distance_unit: str = DEFAULT_DISTANCE_UNIT,
    ) -> None:
        self.entry_id = entry_id
        self.hub_name = hub_name  # exposed as each fire's "hub" attribute

        # `radius` is in whichever unit the person chose; convert once to km,
        # since that's what _haversine_km and the filter below both use.
        self.radius_km = radius * MI_TO_KM if distance_unit == "mi" else radius

        self.center_lat = center_lat if center_lat is not None else hass.config.latitude
        self.center_lon = center_lon if center_lon is not None else hass.config.longitude

        self._session = async_get_clientsession(hass)

        # Fire IDs seen on the previous poll, used to detect new/missing fires.
        self._known_ids: set[str] = set()
        # Consecutive missed polls per fire ID (see MISSING_POLLS_BEFORE_REMOVAL).
        self._missing_counts: dict[str, int] = {}
        # True after the first successful poll, so pre-existing fires at
        # startup aren't treated as "new" (see _async_update_data).
        self._first_refresh_done = False

        self.latest_incident: dict | None = None
        self.newly_added: dict[str, dict] = {}
        self.removed_ids: set[str] = set()

        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(minutes=scan_minutes)
        )

    def seed_known_ids(self, ids: set[str]) -> None:
        """Mark fire IDs as already-known (e.g. from entities left over after a restart)."""
        self._known_ids |= ids

    async def _async_update_data(self):
        """Fetch and parse the feed. Runs on a timer; result becomes `self.data`."""
        try:
            async with async_timeout.timeout(30):
                resp = await self._session.get(API_URL)
                resp.raise_for_status()
                geojson = await resp.json(content_type=None)
        except Exception as err:  # noqa: BLE001 - any fetch failure is reported the same way
            raise UpdateFailed(f"Error fetching CAL FIRE feed: {err}") from err

        incidents: dict[str, dict] = {}
        logged_sample = False
        for feature in geojson.get("features", []):
            props = feature.get("properties", {}) or {}

            if not logged_sample:
                # Undocumented API: log real field names once per poll for troubleshooting.
                _LOGGER.debug("Sample CAL FIRE incident properties: %s", sorted(props.keys()))
                logged_sample = True

            # GeoJSON coordinates are [lon, lat], the opposite of the usual order.
            geometry = feature.get("geometry", {}) or {}
            coords = geometry.get("coordinates")
            lat = props.get("Latitude")
            lon = props.get("Longitude")
            if (lat is None or lon is None) and coords and len(coords) >= 2:
                lon, lat = coords[0], coords[1]
            lat, lon = _to_float(lat), _to_float(lon)

            unique_id = str(
                props.get("UniqueId") or props.get("IncidentId") or props.get("Name")
            )
            if not unique_id:
                continue

            distance_km = (
                _haversine_km(self.center_lat, self.center_lon, lat, lon)
                if lat is not None and lon is not None
                else None
            )
            if self.radius_km and distance_km is not None and distance_km > self.radius_km:
                continue  # outside the configured radius; never becomes an entity

            distance_mi = distance_km * KM_TO_MILES if distance_km is not None else None

            raw_started = _first(props, "Started", "StartedDate")
            started_dt = _parse_started_date(raw_started)
            days_burning = None
            if started_dt is not None:
                days_burning = max((dt_util.utcnow() - started_dt).days, 0)
            elif raw_started:
                _LOGGER.debug("Could not parse started date %r for %s", raw_started, unique_id)

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
                "incident_type": _first(props, "IncidentType", "Type", "IncidentTypeDisplay"),
                "started": raw_started,
                "days_burning": days_burning,
                "updated": _first(props, "Updated", "UpdatedDate"),
                "url": props.get("Url"),
                "latitude": lat,
                "longitude": lon,
                "distance_km": round(distance_km, 1) if distance_km is not None else None,
                "distance_mi": round(distance_mi, 1) if distance_mi is not None else None,
                "hub": self.hub_name,
            }

        # New fires since last poll (skipped on the very first poll, so
        # already-active fires at startup aren't treated as "new").
        if self._first_refresh_done:
            new_ids = set(incidents) - self._known_ids
            self.newly_added = {uid: incidents[uid] for uid in new_ids}
            if new_ids:
                # If several appeared in the same poll, surface the largest as "latest".
                newest = max(new_ids, key=lambda uid: incidents[uid]["acres_burned"] or 0)
                self.latest_incident = incidents[newest]
        else:
            self._first_refresh_done = True
            self.newly_added = {}

        # Fires missing MISSING_POLLS_BEFORE_REMOVAL times in a row are confirmed gone.
        removed_ids = set()
        for uid in self._known_ids:
            if uid in incidents:
                self._missing_counts.pop(uid, None)
                continue
            count = self._missing_counts.get(uid, 0) + 1
            self._missing_counts[uid] = count
            if count >= MISSING_POLLS_BEFORE_REMOVAL:
                removed_ids.add(uid)
                self._missing_counts.pop(uid, None)
        self.removed_ids = removed_ids
        self._known_ids = (self._known_ids - removed_ids) | set(incidents)

        return incidents


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one hub instance (config entry) of this integration."""

    def _get(key: str, default):
        return entry.options.get(key, entry.data.get(key, default))

    coordinator = CalFireCoordinator(
        hass,
        entry.entry_id,
        entry.title,
        _get(CONF_RADIUS, DEFAULT_RADIUS),
        _get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES),
        _get(CONF_CENTER_LATITUDE, None),
        _get(CONF_CENTER_LONGITUDE, None),
        _get(CONF_DISTANCE_UNIT, DEFAULT_DISTANCE_UNIT),
    )

    # Seed tracking with fire entities already registered (e.g. from before a
    # restart), so they're evaluated for removal instead of being invisible to it.
    prefix = f"{DOMAIN}_{entry.entry_id}_"
    ent_reg = async_get_entity_registry(hass)
    known_ids = {
        e.unique_id[len(prefix) :]
        for e in async_entries_for_config_entry(ent_reg, entry.entry_id)
        if e.domain == "sensor"
        and e.unique_id.startswith(prefix)
        and not e.unique_id.endswith("_latest_incident")
    }
    coordinator.seed_known_ids(known_ids)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Changing options doesn't update the running coordinator by itself;
    # reloading re-runs this function with the new values.
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_update))

    return True


async def _async_reload_on_options_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
