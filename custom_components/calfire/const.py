"""Constants for the CAL FIRE Incidents integration."""

DOMAIN = "calfire"

# Display name, editable per hub instance; also exposed as each fire's "hub" attribute.
CONF_NAME = "name"
DEFAULT_NAME = "CAL FIRE Incidents"

# CAL FIRE's internal (undocumented) API for active incidents, as GeoJSON.
API_URL = "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/GeoJsonList?inactive=false"

CONF_RADIUS = "radius"
DEFAULT_RADIUS = 0  # 0 = no distance filtering, show all incidents statewide

CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
DEFAULT_SCAN_INTERVAL_MINUTES = 10

# Optional center-point override. Unset -> Home Assistant's configured home location.
CONF_CENTER_LATITUDE = "center_latitude"
CONF_CENTER_LONGITUDE = "center_longitude"

# Unit the Radius field above is entered in. Doesn't affect the always-present
# distance_km/distance_mi attributes, only how the radius value is interpreted.
CONF_DISTANCE_UNIT = "distance_unit"
DEFAULT_DISTANCE_UNIT = "km"
KM_TO_MILES = 0.621371
MI_TO_KM = 1.609344

# Consecutive missed polls before a fire's entity is removed (vs. a transient API hiccup).
MISSING_POLLS_BEFORE_REMOVAL = 2
