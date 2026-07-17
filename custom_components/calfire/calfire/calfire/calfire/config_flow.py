"""Config flow for CAL FIRE Incidents."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from .const import (
    CONF_RADIUS_KM,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_RADIUS_KM, default=DEFAULT_RADIUS_KM): vol.Coerce(float),
        vol.Optional(
            CONF_SCAN_INTERVAL_MINUTES, default=DEFAULT_SCAN_INTERVAL_MINUTES
        ): vol.Coerce(int),
    }
)


class CalFireConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CAL FIRE Incidents."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(
                title="CAL FIRE Incidents", data=user_input
            )

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)
