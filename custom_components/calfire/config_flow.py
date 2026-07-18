"""Config flow for CAL FIRE Incidents.

A "config flow" is what powers the Settings -> Devices & Services -> Add
Integration UI in Home Assistant — it's a small form-based wizard for
setting up an integration without editing YAML. Ours is intentionally
simple: a single step asking for two optional numbers.

Whatever the user submits ends up in the resulting ConfigEntry's `.data`
dict, which __init__.py reads when setting up the coordinator.
"""
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

# `voluptuous` is the validation library Home Assistant uses for config
# forms: this schema both defines what fields the form has *and* validates/
# coerces whatever the user types (e.g. `vol.Coerce(float)` turns the
# string from a text box into an actual float, raising a validation error
# if that's not possible).
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_RADIUS_KM, default=DEFAULT_RADIUS_KM): vol.Coerce(float),
        vol.Optional(
            CONF_SCAN_INTERVAL_MINUTES, default=DEFAULT_SCAN_INTERVAL_MINUTES
        ): vol.Coerce(int),
    }
)


class CalFireConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handles the setup wizard for this integration.

    Home Assistant discovers this class automatically because
    manifest.json has `"config_flow": true` and the class is registered
    against our DOMAIN via the `domain=DOMAIN` keyword above.
    """

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """The (only) step in this flow: show a form, then save the result.

        Home Assistant calls this once with `user_input=None` to display
        the empty form, then calls it again with `user_input` populated
        once the user submits it.
        """
        if user_input is not None:
            # The user has submitted the form (and voluptuous has already
            # validated/coerced it against STEP_USER_SCHEMA). Create the
            # actual config entry — this is what triggers __init__.py's
            # `async_setup_entry` to run.
            return self.async_create_entry(
                title="CAL FIRE Incidents", data=user_input
            )

        # First time through: show the form and wait for submission.
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)
