# CAL FIRE Incidents (custom Home Assistant integration)

Creates one sensor entity per active California wildfire incident, pulled from
CAL FIRE's public GeoJSON feed:
https://incidents.fire.ca.gov/umbraco/api/IncidentApi/GeoJsonList?inactive=false

Each entity's state is acres burned. Attributes include county, admin unit,
incident type, percent contained, start/update timestamps, source URL,
latitude/longitude, and distance from your Home Assistant location (if a
radius filter is set). Because latitude/longitude are exposed as attributes,
these entities also show up on the built-in Lovelace **Map** card.

New fires get their own entity automatically as they appear in the feed.
When a fire drops out of the feed (contained/closed and removed by CAL
FIRE), its entity is removed from Home Assistant entirely — but only after
it's been missing from the feed for **2 consecutive polls in a row**
(`MISSING_POLLS_BEFORE_REMOVAL` in `const.py`), so a brief CAL FIRE API
hiccup doesn't delete an entity for a fire that's actually still burning.
Before that threshold is hit, the entity is marked `unavailable` rather
than removed. Recorder history for a removed entity isn't deleted — it's
just no longer surfaced as a live entity, so your entity list stays clean
instead of accumulating dead fires forever.

## Getting notified about new fires

There's a dedicated entity for this: **`sensor.calfire_latest_incident`**.
Its state is always the name of whichever fire was most recently detected
in the feed (starting as `None` until the first new fire shows up after HA
starts). Attributes carry the same detail as the per-fire sensors: county,
acres, containment, URL, lat/lon, etc.

Point an automation's `state` trigger at it — no event filtering needed:

```yaml
alias: New CAL FIRE incident notification
trigger:
  - platform: state
    entity_id: sensor.calfire_latest_incident
condition:
  - condition: template
    value_template: "{{ trigger.to_state.state != 'None' }}"
action:
  - service: notify.mobile_app_YOUR_PHONE
    data:
      title: "New wildfire: {{ trigger.to_state.state }}"
      message: >
        {{ trigger.to_state.attributes.county }} county,
        {{ trigger.to_state.attributes.acres_burned }} acres,
        {{ trigger.to_state.attributes.percent_contained }}% contained.
      data:
        url: "{{ trigger.to_state.attributes.url }}"
```

The `condition` just guards against the very first state ever recorded
(`None`) firing a bogus notification — every change after that is a real
new fire.

If more than one fire appears in the same poll cycle, this entity surfaces
whichever has burned the most acres; the others are still tracked as their
own per-fire entities, so nothing is lost, just not reflected here.

### Alternative: a custom event

The integration also fires a `calfire_new_incident` event for *every* new
fire (not just the single most-recent one), if you'd rather trigger off
that instead:

```yaml
alias: New CAL FIRE incident notification (event-based)
trigger:
  - platform: event
    event_type: calfire_new_incident
action:
  - service: notify.mobile_app_YOUR_PHONE
    data:
      title: "New wildfire: {{ trigger.event.data.name }}"
      message: >
        {{ trigger.event.data.county }} county,
        {{ trigger.event.data.acres_burned }} acres,
        {{ trigger.event.data.percent_contained }}% contained.
      data:
        url: "{{ trigger.event.data.url }}"
```

Available fields on `trigger.event.data`: `unique_id`, `name`, `county`,
`admin_unit`, `incident_type`, `acres_burned`, `percent_contained`,
`distance_km`, `url`, `latitude`, `longitude`.

## Installation via HACS (recommended once this is on GitHub)

1. Push this folder to a GitHub repo (see "Publishing to GitHub" below).
2. In Home Assistant: **HACS → Integrations → ⋮ → Custom repositories**.
3. Add your repo URL, category **Integration**.
4. Find "CAL FIRE Incidents" in HACS and install it.
5. Restart Home Assistant.
6. **Settings → Devices & Services → Add Integration**, search for
   "CAL FIRE Incidents", and add it.

## Manual installation (no HACS)

1. Copy the `custom_components/calfire` folder into your Home Assistant
   `config/custom_components/` directory, so you end up with:
   `config/custom_components/calfire/__init__.py`, etc.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration**, search for
   "CAL FIRE Incidents", and add it.

## Troubleshooting missing/null attributes

CAL FIRE's endpoint is internal/undocumented, so field names have been
reverse-engineered rather than pulled from an official spec. If you notice
an attribute that's always `null` even though CAL FIRE's own incident page
shows a value, enable debug logging to see the raw field names being sent:

```yaml
logger:
  default: warning
  logs:
    custom_components.calfire: debug
```

After a restart, check the log for a line like `Sample CAL FIRE incident
properties: [...]` — that's the actual list of field names in the current
feed, which can be compared against what `__init__.py` looks for.

## Publishing to GitHub (required for HACS)

HACS installs from a GitHub repository, so:

1. Replace `carlkhenderson` in `custom_components/calfire/manifest.json`
   and `LICENSE` with your actual details.
2. Create a new **public** repo on GitHub, e.g. `ha-calfire`.
3. From this folder:
   ```bash
   git remote add origin https://github.com/carlkhenderson/ha-calfire.git
   git branch -M main
   git push -u origin main
   ```
4. The included `.github/workflows/validate.yml` runs `hassfest` and the
   HACS validation action on every push — check the Actions tab after
   pushing to confirm both pass (HACS requires this for a repo to be
   addable, and it catches manifest/structure mistakes early).
4. Optionally set:
   - **Radius (km)**: only show fires within this distance of your HA home
     location. Leave at `0` for all active incidents statewide.
   - **Scan interval (minutes)**: how often to poll the feed. CAL FIRE
     doesn't update the underlying data much faster than every 15–30 min
     during an active incident, so the default of 10 minutes is reasonable;
     you don't need to go much lower.

## Notes / things you may want to tweak

- The feed only includes fires CAL FIRE currently tracks (roughly 10+ acre
  wildfires and other significant incidents) — not every reported ignition.
- If you'd rather have the sensor's *state* be percent contained instead of
  acres burned, swap `native_value` in `sensor.py`.
- If you want entities removed entirely (rather than marked unavailable)
  once a fire drops off the feed, that logic can be added in `sensor.py`
  using the entity registry — ask if you'd like that version.
