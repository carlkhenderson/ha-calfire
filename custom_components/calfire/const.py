"""Constants for the CAL FIRE Incidents integration.

Keeping these in one small file (rather than scattered as magic
numbers/strings throughout the code) makes them easy to find and tweak,
and is a standard Home Assistant integration convention.
"""

# Must match the folder name (custom_components/calfire) and is used
# throughout the integration as a namespace — for hass.data keys, unique_id
# prefixes, and registering the config flow.
DOMAIN = "calfire"

# A user-editable display name, so multiple instances of this integration
# (e.g. one centered on your home, one on a relative's) can be told apart
# in Settings -> Devices & Services, and so fire entities can carry which
# hub they came from as an attribute for dashboard filtering.
CONF_NAME = "name"
DEFAULT_NAME = "CAL FIRE Incidents"

# CAL FIRE's own internal API endpoint (built for their website, not for
# third-party use) that returns currently-active incidents as GeoJSON.
API_URL = "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/GeoJsonList?inactive=false"

# Keys used in the config entry's `.data` dict — i.e. whatever the setup
# form in config_flow.py collects, and what __init__.py reads back out.
# NOTE: the stored key is still "radius_km" for backward compatibility
# with existing installs, but the *value* is interpreted according to
# CONF_DISTANCE_UNIT below (km or mi) — see CalFireCoordinator.__init__.
CONF_RADIUS_KM = "radius_km"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
# Optional override for the radius filter's center point. If either is left
# unset, the coordinator falls back to Home Assistant's configured home
# location (Settings -> System -> General).
CONF_CENTER_LATITUDE = "center_latitude"
CONF_CENTER_LONGITUDE = "center_longitude"
# Which unit the person is entering the radius in ("km" or "mi") — this is
# a units-of-input choice, not a display preference. Every fire's distance
# is always exposed as both `distance_km` and `distance_mi` attributes
# regardless of this setting.
CONF_DISTANCE_UNIT = "distance_unit"
DEFAULT_DISTANCE_UNIT = "km"
KM_TO_MILES = 0.621371
MI_TO_KM = 1.609344

# Default values offered in the setup form if the user doesn't change them.
DEFAULT_RADIUS_KM = 0  # 0 = no distance filtering, show all incidents statewide
DEFAULT_SCAN_INTERVAL_MINUTES = 10

# How many consecutive polls a fire can be missing from the feed before we
# remove its entity outright. Guards against a transient CAL FIRE API hiccup
# (empty/partial response) being mistaken for a fire actually closing out.
MISSING_POLLS_BEFORE_REMOVAL = 2
