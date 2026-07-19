"""Config flow for CAL FIRE Incidents.

Two related flows sharing one schema (`_schema` below):
- `CalFireConfigFlow`: initial setup wizard.
- `CalFireOptionsFlow`: the "Configure" gear icon, for changing settings later.

Field labels come from translations/en.json, not the raw field names below.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_CENTER_LATITUDE,
    CONF_CENTER_LONGITUDE,
    CONF_DISTANCE_UNIT,
    CONF_NAME,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_DISTANCE_UNIT,
    DEFAULT_NAME,
    DEFAULT_RADIUS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)


def _schema(current: dict) -> vol.Schema:
    """Setup/options form fields, pre-filled from `current` (empty for initial setup)."""
    return vol.Schema(
        {
            vol.Optional(CONF_NAME, default=current.get(CONF_NAME, DEFAULT_NAME)): str,
            vol.Optional(
                CONF_DISTANCE_UNIT, default=current.get(CONF_DISTANCE_UNIT, DEFAULT_DISTANCE_UNIT)
            ): vol.In(["km", "mi"]),
            vol.Optional(
                CONF_RADIUS, default=current.get(CONF_RADIUS, DEFAULT_RADIUS)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_SCAN_INTERVAL_MINUTES,
                default=current.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES),
            ): vol.Coerce(int),
            # No default on these two: leaving them blank (vs. e.g. 0,0) means
            # "use Home Assistant's home location" (see __init__.py).
            vol.Optional(
                CONF_CENTER_LATITUDE, **_maybe_default(current, CONF_CENTER_LATITUDE)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_CENTER_LONGITUDE, **_maybe_default(current, CONF_CENTER_LONGITUDE)
            ): vol.Coerce(float),
        }
    )


def _maybe_default(current: dict, key: str) -> dict:
    """{"default": value} if `key` is already set, else {} (leaves the field blank)."""
    return {"default": current[key]} if current.get(key) is not None else {}


class CalFireConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup wizard."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            # Using the chosen Name as the entry title is what makes multiple
            # hub instances distinguishable in Settings -> Devices & Services.
            name = user_input.get(CONF_NAME) or DEFAULT_NAME
            return self.async_create_entry(title=name, data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> "CalFireOptionsFlow":
        return CalFireOptionsFlow()


class CalFireOptionsFlow(config_entries.OptionsFlow):
    """The "Configure" flow for an already-set-up entry.

    No `__init__` here: `self.config_entry` is provided automatically by the
    base class. (Manually assigning it, as older examples do, raises as of
    Home Assistant 2025.12.)
    """

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            # Options-flow saves go to entry.options, not entry.data (see
            # __init__.py for how they're merged) and don't update the title
            # on their own, so rename explicitly if the Name field changed.
            new_name = user_input.get(CONF_NAME) or DEFAULT_NAME
            if new_name != self.config_entry.title:
                self.hass.config_entries.async_update_entry(self.config_entry, title=new_name)
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
