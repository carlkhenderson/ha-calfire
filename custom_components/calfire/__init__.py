"""The CAL FIRE Incidents integration."""
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
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _to_float(value):
    """CAL FIRE sends numbers as display strings like '1,234' or '25%'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("%", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


class CalFireCoordinator(DataUpdateCoordinator):
    """Fetch and parse the CAL FIRE GeoJSON incident feed."""

    def __init__(self, hass: HomeAssistant, radius_km: float, scan_minutes: int) -> None:
        self.radius_km = radius_km
        self.home_lat = hass.config.latitude
        self.home_lon = hass.config.longitude
        session = async_get_clientsession(hass)
        self._session = session
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_minutes),
        )

    async def _async_update_data(self):
        try:
            async with async_timeout.timeout(30):
                resp = await self._session.get(API_URL)
                resp.raise_for_status()
                geojson = await resp.json(content_type=None)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Error fetching CAL FIRE feed: {err}") from err

        incidents: dict[str, dict] = {}
        for feature in geojson.get("features", []):
            props = feature.get("properties", {}) or {}
            geometry = feature.get("geometry", {}) or {}
            coords = geometry.get("coordinates")

            lat = props.get("Latitude")
            lon = props.get("Longitude")
            if (lat is None or lon is None) and coords and len(coords) >= 2:
                lon, lat = coords[0], coords[1]

            lat = _to_float(lat)
            lon = _to_float(lon)

            unique_id = str(
                props.get("UniqueId") or props.get("IncidentId") or props.get("Name")
            )
            if not unique_id:
                continue

            distance_km = None
            if lat is not None and lon is not None:
                distance_km = _haversine_km(self.home_lat, self.home_lon, lat, lon)

            if self.radius_km and distance_km is not None and distance_km > self.radius_km:
                continue

            incidents[unique_id] = {
                "unique_id": unique_id,
                "name": props.get("Name") or "Unknown Incident",
                "acres_burned": _to_float(props.get("AcresBurnedDisplay")),
                "percent_contained": _to_float(props.get("PercentContainedDisplay")),
                "county": props.get("CountiesList"),
                "admin_unit": props.get("AdminUnit"),
                "incident_type": props.get("IncidentTypeDisplay"),
                "started": props.get("StartedDate"),
                "updated": props.get("UpdatedDate"),
                "url": props.get("Url"),
                "is_active": props.get("IsActive"),
                "latitude": lat,
                "longitude": lon,
                "distance_km": round(distance_km, 1) if distance_km is not None else None,
            }

        return incidents


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CAL FIRE Incidents from a config entry."""
    radius_km = entry.data.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM)
    scan_minutes = entry.data.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)

    coordinator = CalFireCoordinator(hass, radius_km, scan_minutes)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
