"""Sensor platform for CAL FIRE Incidents — one entity per active fire."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up CAL FIRE sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known_ids: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        new_entities = []
        for unique_id, incident in coordinator.data.items():
            if unique_id not in known_ids:
                known_ids.add(unique_id)
                new_entities.append(CalFireIncidentSensor(coordinator, unique_id))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class CalFireIncidentSensor(CoordinatorEntity, SensorEntity):
    """Represents a single active CAL FIRE incident."""

    _attr_icon = "mdi:fire"
    _attr_native_unit_of_measurement = "acres"
    def __init__(self, coordinator, unique_id: str) -> None:
        super().__init__(coordinator)
        self._incident_id = unique_id
        self._attr_unique_id = f"calfire_{unique_id}"

    @property
    def _incident(self) -> dict | None:
        return self.coordinator.data.get(self._incident_id)

    @property
    def available(self) -> bool:
        # Entity becomes unavailable once CAL FIRE stops listing the fire as active,
        # rather than disappearing outright, so history is preserved.
        return super().available and self._incident is not None

    @property
    def name(self) -> str:
        incident = self._incident
        return incident["name"] if incident else self._incident_id

    @property
    def native_value(self):
        incident = self._incident
        return incident["acres_burned"] if incident else None

    @property
    def extra_state_attributes(self):
        incident = self._incident
        if not incident:
            return {}
        return {
            "county": incident["county"],
            "admin_unit": incident["admin_unit"],
            "incident_type": incident["incident_type"],
            "percent_contained": incident["percent_contained"],
            "started": incident["started"],
            "updated": incident["updated"],
            "distance_km": incident["distance_km"],
            "url": incident["url"],
            "latitude": incident["latitude"],
            "longitude": incident["longitude"],
        }
