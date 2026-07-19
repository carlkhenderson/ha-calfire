"""Config flow for CAL FIRE Incidents.

A "config flow" is what powers the Settings -> Devices & Services -> Add
Integration UI in Home Assistant — it's a small form-based wizard for
setting up an integration without editing YAML. This file defines two
related but separate flows:

- `CalFireConfigFlow`: the initial setup wizard, run once when you first
  add the integration.
- `CalFireOptionsFlow`: the "Configure" (gear icon) flow, which lets you
  change those same settings later without removing and re-adding the
  integration.

Both share the same form fields, defined once in `_schema()` below.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_CENTER_LATITUDE,
    CONF_CENTER_LONGITUDE,
    CONF_DISTANCE_UNIT,
    CONF_RADIUS_KM,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_DISTANCE_UNIT,
    DEFAULT_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)


def _schema(current: dict) -> vol.Schema:
    """Build the form schema, pre-filling defaults from `current` values.

    Used for both the initial setup form (where `current` is empty, so
    everything falls back to the DEFAULT_* constants) and the options form
    (where `current` holds whatever's already configured, so re-opening
    "Configure" shows your existing settings rather than blank fields).

    `voluptuous` is the validation library Home Assistant uses for config
    forms: this schema both defines what fields the form has *and*
    validates/coerces whatever the user types (e.g. `vol.Coerce(float)`
    turns the string from a text box into an actual float).
    """
    return vol.Schema(
        {
            vol.Optional(
                CONF_RADIUS_KM, default=current.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_SCAN_INTERVAL_MINUTES,
                default=current.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES),
            ): vol.Coerce(int),
            vol.Optional(
                CONF_DISTANCE_UNIT,
                default=current.get(CONF_DISTANCE_UNIT, DEFAULT_DISTANCE_UNIT),
            ): vol.In(["km", "mi"]),
            # These two are deliberately left with NO default. An
            # `vol.Optional` field without a `default=` simply doesn't
            # appear in the submitted data at all if the user leaves it
            # blank — which is how we distinguish "use home location" from
            # "center on lat 0, lon 0". If the user previously set a value,
            # we still pre-fill it here so editing doesn't lose it.
            vol.Optional(
                CONF_CENTER_LATITUDE, **_maybe_default(current, CONF_CENTER_LATITUDE)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_CENTER_LONGITUDE, **_maybe_default(current, CONF_CENTER_LONGITUDE)
            ): vol.Coerce(float),
        }
    )


def _maybe_default(current: dict, key: str) -> dict:
    """Return {"default": value} if `key` is set in `current`, else {}.

    A small helper so we only pass `default=...` to `vol.Optional` when
    there's actually a previous value to show — voluptuous/HA's form
    renderer treats a field with no `default` kwarg at all as a genuinely
    blank/optional box, which is what we want for "unset = use home
    location".
    """
    if key in current and current[key] is not None:
        return {"default": current[key]}
    return {}


class CalFireConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handles the initial setup wizard for this integration.

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
            # validated/coerced it against the schema). Create the actual
            # config entry — this is what triggers __init__.py's
            # `async_setup_entry` to run.
            return self.async_create_entry(title="CAL FIRE Incidents", data=user_input)

        # First time through: show the form and wait for submission.
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> "CalFireOptionsFlow":
        """Tell Home Assistant how to get an options flow for an existing entry.

        This is what makes the "Configure" gear icon show up on the
        integration's card in Settings -> Devices & Services.
        """
        return CalFireOptionsFlow()


class CalFireOptionsFlow(config_entries.OptionsFlow):
    """Handles the "Configure" flow for an already-set-up entry.

    Deliberately has no `__init__` here. Older Home Assistant examples had
    options flows manually store `self.config_entry = config_entry` — that
    pattern was deprecated and, as of Home Assistant 2025.12, actually
    raises an error rather than just a warning. The base `OptionsFlow`
    class already provides `self.config_entry` automatically, so we simply
    use it below without ever assigning to it ourselves.
    """

    async def async_step_init(self, user_input=None):
        """The (only) step: show the current settings, then save changes."""
        if user_input is not None:
            # Options-flow entries conventionally save into `entry.options`
            # rather than `entry.data` (which holds the *initial* setup
            # values) — see __init__.py for how the two are merged when
            # read back. Passing title="" here is normal/expected for an
            # options flow; only the initial config flow sets a real title.
            return self.async_create_entry(title="", data=user_input)

        # Show whatever's already configured, checking options first (a
        # previous "Configure" save) and falling back to the original
        # setup data, so re-opening this form doesn't reset anything.
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
