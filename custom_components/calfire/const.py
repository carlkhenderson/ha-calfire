"""Constants for the CAL FIRE Incidents integration.

Keeping these in one small file (rather than scattered as magic
numbers/strings throughout the code) makes them easy to find and tweak,
and is a standard Home Assistant integration convention.
"""

# Must match the folder name (custom_components/calfire) and is used
# throughout the integration as a namespace — for hass.data keys, unique_id
# prefixes, and registering the config flow.
DOMAIN = "calfire"

# CAL FIRE's own internal API endpoint (built for their website, not for
# third-party use) that returns currently-active incidents as GeoJSON.
API_URL = "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/GeoJsonList?inactive=false"

# Keys used in the config entry's `.data` dict — i.e. whatever the setup
# form in config_flow.py collects, and what __init__.py reads back out.
CONF_RADIUS_KM = "radius_km"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
# Optional override for the radius filter's center point. If either is left
# unset, the coordinator falls back to Home Assistant's configured home
# location (Settings -> System -> General).
CONF_CENTER_LATITUDE = "center_latitude"
CONF_CENTER_LONGITUDE = "center_longitude"

# Default values offered in the setup form if the user doesn't change them.
DEFAULT_RADIUS_KM = 0  # 0 = no distance filtering, show all incidents statewide
DEFAULT_SCAN_INTERVAL_MINUTES = 10

# How many consecutive polls a fire can be missing from the feed before we
# remove its entity outright. Guards against a transient CAL FIRE API hiccup
# (empty/partial response) being mistaken for a fire actually closing out.
MISSING_POLLS_BEFORE_REMOVAL = 2
