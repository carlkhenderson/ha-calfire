"""Sensor platform for CAL FIRE Incidents.

- `CalFireIncidentSensor`: one entity per active fire, created/removed as
  fires appear/disappear in the feed.
- `CalFireLatestIncidentSensor`: a single, permanent entity reflecting
  whichever fire was most recently discovered, for automations that want
  one stable entity_id instead of reacting to dynamically-created ones.

Both read from the `CalFireCoordinator` (__init__.py) rather than fetching anything themselves.
"""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _attributes(incident: dict, *, exclude: set[str]) -> dict:
    """Incident fields as entity attributes, minus whichever are used elsewhere
    (e.g. already the entity's state or name)."""
    return {k: v for k, v in incident.items() if k not in exclude}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create sensor entities for this config entry, and keep them in sync."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [CalFireIncidentSensor(coordinator, uid) for uid in coordinator.data]
    entities.append(CalFireLatestIncidentSensor(coordinator, entry.entry_id))
    async_add_entities(entities)

    @callback
    def _handle_update() -> None:
        """Runs after every poll: add entities for new fires, remove for closed ones.

        Each half is wrapped separately so a bug in one can't block the other.
        `coordinator.newly_added`/`removed_ids` only ever list fires we haven't
        already created/removed entities for, so no local bookkeeping is needed here.
        """
        try:
            new_entities = [
                CalFireIncidentSensor(coordinator, uid) for uid in coordinator.newly_added
            ]
            if new_entities:
                async_add_entities(new_entities)
            for incident in coordinator.newly_added.values():
                hass.bus.async_fire("calfire_new_incident", incident)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error adding entities for new CAL FIRE incidents")

        try:
            ent_reg = async_get_entity_registry(hass)
            for unique_id in coordinator.removed_ids:
                entity_id = ent_reg.async_get_entity_id(
                    "sensor", DOMAIN, f"calfire_{coordinator.entry_id}_{unique_id}"
                )
                if entity_id:
                    ent_reg.async_remove(entity_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error removing entities for closed CAL FIRE incidents")

    entry.async_on_unload(coordinator.async_add_listener(_handle_update))


class CalFireIncidentSensor(CoordinatorEntity, SensorEntity):
    """One entity per active CAL FIRE incident. State is acres burned."""

    _attr_icon = "mdi:fire"
    _attr_native_unit_of_measurement = "acres"

    def __init__(self, coordinator, unique_id: str) -> None:
        super().__init__(coordinator)
        self._incident_id = unique_id
        self._attr_unique_id = f"calfire_{coordinator.entry_id}_{unique_id}"
        # Cached so attributes don't go blank while this fire is briefly
        # missing from the feed (see `_incident`).
        self._last_known: dict | None = coordinator.data.get(unique_id)

    @property
    def _incident(self) -> dict | None:
        """Current incident data, or the last-known copy if it's temporarily
        missing (within its removal grace period) rather than actually gone."""
        current = self.coordinator.data.get(self._incident_id)
        if current is not None:
            self._last_known = current
            return current
        return self._last_known

    @property
    def available(self) -> bool:
        # Checks coordinator.data directly (not `_incident`, which may fall back
        # to cached data) so the entity still reports unavailable during the
        # grace period even though its attributes stay populated.
        return super().available and self.coordinator.data.get(self._incident_id) is not None

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
        return _attributes(incident, exclude={"unique_id", "name", "acres_burned"})


class CalFireLatestIncidentSensor(CoordinatorEntity, SensorEntity):
    """A single, permanent entity reflecting whichever fire was most recently
    discovered — a stable entity_id for automations to watch."""

    _attr_icon = "mdi:fire-alert"
    _attr_name = "Latest Incident"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"calfire_{entry_id}_latest_incident"

    @property
    def native_value(self):
        # "None" (the string) until the first new fire is seen after HA
        # starts, so automations can reliably check state != 'None'.
        incident = self.coordinator.latest_incident
        return incident["name"] if incident else "None"

    @property
    def extra_state_attributes(self):
        incident = self.coordinator.latest_incident
        if not incident:
            return {}
        return _attributes(incident, exclude={"unique_id", "name"})
