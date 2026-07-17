"""Constants for the CAL FIRE Incidents integration."""

DOMAIN = "calfire"

API_URL = "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/GeoJsonList?inactive=false"

CONF_RADIUS_KM = "radius_km"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"

DEFAULT_RADIUS_KM = 0  # 0 = no distance filtering, show all incidents statewide
DEFAULT_SCAN_INTERVAL_MINUTES = 10
