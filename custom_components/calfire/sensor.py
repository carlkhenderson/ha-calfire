"""Sensor platform for CAL FIRE Incidents.

This file defines the actual Home Assistant *entities* — the things that
show up as `sensor.something` in your dashboard. There are two kinds:

- `CalFireIncidentSensor`: one entity per currently-active fire, created
  and removed dynamically as fires appear/disappear in CAL FIRE's feed.
- `CalFireLatestIncidentSensor`: a single, always-present entity
  (`sensor.calfire_latest_incident`) that always reflects whichever fire
  was most recently discovered — handy for automations that just want to
  watch one stable entity_id instead of reacting to dynamically-created
  ones.

All of these read their data from the `CalFireCoordinator` defined in
__init__.py (via `self.coordinator.data`, `self.coordinator.latest_incident`,
etc.) rather than fetching anything themselves.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create sensor entities for this config entry, and keep them in sync.

    Home Assistant calls this once, when the integration (or this specific
    platform of it) is being set up. `async_add_entities` is a callback we
    use both here (for the initial batch) and later (each time a new fire
    shows up) to register entities with Home Assistant.
    """
    # The coordinator was created and stored in __init__.py's
    # `async_setup_entry`; look it up so we can read its data and subscribe
    # to its updates.
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # Track which incident IDs we've already created an entity for, so we
    # don't accidentally create duplicates on later updates. Seeded with
    # whatever's already in the feed at startup.
    known_ids: set[str] = set(coordinator.data)

    # Build the initial batch of entities: one per fire currently in the
    # feed, plus the single "latest incident" singleton entity.
    entities = [CalFireIncidentSensor(coordinator, uid) for uid in coordinator.data]
    entities.append(CalFireLatestIncidentSensor(coordinator, entry.entry_id))
    async_add_entities(entities)

    @callback
    def _handle_update() -> None:
        """Run after every coordinator refresh (i.e. every poll).

        Decorated with `@callback` because it does no I/O and must run
        synchronously on Home Assistant's event loop — this is how
        `DataUpdateCoordinator` listeners are expected to be written.
        """
        # --- Create entities for any fires that are new this poll ---
        # `coordinator.newly_added` is computed in __init__.py's
        # `_async_update_data` and contains only fires that weren't present
        # on the previous poll (and is empty on the very first poll, so we
        # don't treat pre-existing fires as "new").
        new_entities = []
        for unique_id, incident in coordinator.newly_added.items():
            if unique_id not in known_ids:
                known_ids.add(unique_id)
                new_entities.append(CalFireIncidentSensor(coordinator, unique_id))

                # Fire a Home Assistant event carrying this fire's details.
                # Automations can trigger on event_type "calfire_new_incident"
                # as an alternative to watching sensor.calfire_latest_incident.
                hass.bus.async_fire(
                    "calfire_new_incident",
                    {
                        "unique_id": unique_id,
                        "name": incident["name"],
                        "county": incident["county"],
                        "admin_unit": incident["admin_unit"],
                        "incident_type": incident["incident_type"],
                        "acres_burned": incident["acres_burned"],
                        "percent_contained": incident["percent_contained"],
                        "distance_km": incident["distance_km"],
                        "url": incident["url"],
                        "latitude": incident["latitude"],
                        "longitude": incident["longitude"],
                    },
                )
        if new_entities:
            async_add_entities(new_entities)

        # --- Remove entities for fires the coordinator has confirmed are gone ---
        # `coordinator.removed_ids` only contains fires that have been
        # missing from the feed for several consecutive polls in a row (see
        # MISSING_POLLS_BEFORE_REMOVAL in const.py) — not just a single
        # missed poll, which could be a transient API hiccup.
        if coordinator.removed_ids:
            # The entity registry is Home Assistant's persistent database
            # of every entity that's ever been created (entity_id <->
            # unique_id mappings, custom names, etc). We use it here to
            # look up the entity_id for a unique_id, then delete it —
            # actually removing the entity from Home Assistant entirely,
            # rather than just leaving it stuck in an "unavailable" state.
            ent_reg = async_get_entity_registry(hass)
            for unique_id in coordinator.removed_ids:
                known_ids.discard(unique_id)
                entity_id = ent_reg.async_get_entity_id(
                    "sensor", DOMAIN, f"calfire_{unique_id}"
                )
                if entity_id:
                    ent_reg.async_remove(entity_id)

    # Subscribe `_handle_update` to run after every coordinator refresh.
    # `entry.async_on_unload(...)` registers the returned "unsubscribe"
    # function to be called automatically if the integration is ever
    # unloaded/reloaded, so we don't leak a listener that keeps firing
    # after the config entry is gone.
    entry.async_on_unload(coordinator.async_add_listener(_handle_update))


class CalFireIncidentSensor(CoordinatorEntity, SensorEntity):
    """One entity representing a single active CAL FIRE incident.

    Inherits from `CoordinatorEntity`, which handles subscribing to the
    coordinator and automatically triggering a state update in the UI
    whenever the coordinator refreshes — we don't need to write any of
    that plumbing ourselves, just define what data to show.
    """

    # Class-level attributes are Home Assistant's standard way of setting
    # simple, unchanging entity properties. Prefixing with `_attr_` is a
    # naming convention the base entity classes look for automatically.
    _attr_icon = "mdi:fire"
    _attr_native_unit_of_measurement = "acres"

    def __init__(self, coordinator, unique_id: str) -> None:
        super().__init__(coordinator)
        # Which fire (by CAL FIRE's own ID) this particular entity represents.
        self._incident_id = unique_id
        # Home Assistant uses `unique_id` (not entity_id or name) as the
        # permanent, stable identifier for an entity across restarts and
        # renames. Prefixed with "calfire_" to keep it clearly namespaced.
        self._attr_unique_id = f"calfire_{unique_id}"

    @property
    def _incident(self) -> dict | None:
        """Look up this fire's current details from the coordinator.

        Returns None if this fire is no longer in `coordinator.data` (i.e.
        it's dropped out of the feed but hasn't yet hit the "missing long
        enough to remove" threshold — see `available` below).
        """
        return self.coordinator.data.get(self._incident_id)

    @property
    def available(self) -> bool:
        """Whether this entity should show as available in the UI.

        `super().available` covers the coordinator-level checks (e.g. "did
        the last fetch fail entirely?"). We additionally require that this
        specific fire still be present in the latest data — if it's
        temporarily missing (within its grace period), the entity shows as
        "unavailable" rather than displaying stale numbers. If it's missing
        for long enough, sensor setup's `_handle_update` above removes the
        entity outright instead of leaving it unavailable forever.
        """
        return super().available and self._incident is not None

    @property
    def name(self) -> str:
        incident = self._incident
        return incident["name"] if incident else self._incident_id

    @property
    def native_value(self):
        """The entity's main state value — acres burned, in this case."""
        incident = self._incident
        return incident["acres_burned"] if incident else None

    @property
    def extra_state_attributes(self):
        """Extra data shown as attributes alongside the main state.

        These aren't the entity's "state" (that's native_value, above) but
        show up in Developer Tools -> States and can be referenced in
        templates as e.g. `state_attr('sensor.x', 'county')`.
        """
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


class CalFireLatestIncidentSensor(CoordinatorEntity, SensorEntity):
    """A single entity that always reflects the most recently discovered fire.

    Unlike CalFireIncidentSensor, there's only ever one of these per config
    entry, and it's never removed — its state just changes to reflect
    whatever the newest fire is. This makes writing automations simpler:
    point a `state` trigger at `sensor.calfire_latest_incident` instead of
    needing to react to dynamically-created per-fire entities or listen for
    a custom event.
    """

    _attr_icon = "mdi:fire-alert"
    # `_attr_name` here is a fixed, human-readable name (as opposed to
    # CalFireIncidentSensor, where the name varies per fire and is
    # implemented as a property instead).
    _attr_name = "Latest Incident"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        # Tied to the config entry rather than any one fire, since this
        # entity represents "whichever fire is newest" over time, not a
        # specific fire.
        self._attr_unique_id = f"calfire_{entry_id}_latest_incident"

    @property
    def native_value(self):
        incident = self.coordinator.latest_incident
        # `latest_incident` starts out as None and stays that way until the
        # first new fire is detected after Home Assistant starts up (see
        # __init__.py) — we surface that as the literal string "None"
        # rather than Home Assistant's own "unknown"/"unavailable" states,
        # so automations can reliably check `state != 'None'`.
        return incident["name"] if incident else "None"

    @property
    def extra_state_attributes(self):
        incident = self.coordinator.latest_incident
        if not incident:
            return {}
        return {
            "county": incident["county"],
            "admin_unit": incident["admin_unit"],
            "incident_type": incident["incident_type"],
            "acres_burned": incident["acres_burned"],
            "percent_contained": incident["percent_contained"],
            "started": incident["started"],
            "updated": incident["updated"],
            "distance_km": incident["distance_km"],
            "url": incident["url"],
            "latitude": incident["latitude"],
            "longitude": incident["longitude"],
        }
