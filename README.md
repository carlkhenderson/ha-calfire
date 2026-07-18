# CAL FIRE Incidents

A Home Assistant integration that creates one sensor entity per active
California wildfire incident, pulled from CAL FIRE's incident feed.

## What you get

- One `sensor` entity per currently-active fire, automatically created as
  new fires appear and removed once CAL FIRE stops listing them.
- A single `sensor.calfire_latest_incident` entity that always reflects
  whichever fire was most recently detected — useful for automations (see
  below).
- A `calfire_new_incident` event fired for every new fire, as an
  alternative way to trigger automations.

Each per-fire entity's state is acres burned, with attributes for county,
admin unit, incident type, percent contained, start/update timestamps,
source URL, and latitude/longitude. Because latitude/longitude are exposed
as attributes, these entities also show up on the built-in Lovelace **Map**
card.

## Installation

### Via HACS

1. In Home Assistant: **HACS → Integrations → ⋮ → Custom repositories**.
2. Add this repository's URL, category **Integration**.
3. Find "CAL FIRE Incidents" in HACS and install it.
4. Restart Home Assistant.
5. **Settings → Devices & Services → Add Integration**, search for
   "CAL FIRE Incidents", and add it.

### Manual

1. Copy the `custom_components/calfire` folder into your Home Assistant
   `config/custom_components/` directory, so you end up with
   `config/custom_components/calfire/__init__.py`, etc.
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration**, search for
   "CAL FIRE Incidents", and add it.

## Configuration

During setup (and later — see below) you can optionally set:

- **Radius (km)**: only show fires within this distance of the center
  point. Leave at `0` for all active incidents statewide.
- **Scan interval (minutes)**: how often to poll the feed. CAL FIRE
  doesn't update the underlying data much faster than every 15–30 minutes
  during an active incident, so the default of 10 minutes is reasonable —
  you don't need to go much lower.
- **Center latitude / longitude**: leave both blank to use your Home
  Assistant instance's configured home location (Settings → System →
  General) as the center for the radius filter and each fire's
  `distance_km` attribute. Set both to center on somewhere else instead —
  useful if your HA server isn't physically where you actually want
  "nearby" measured from (a vacation property, a family member's house,
  etc).

### Changing settings later

All of the above can be changed after setup without removing the
integration: go to **Settings → Devices & Services**, find "CAL FIRE
Incidents", and click **Configure**. Changes take effect immediately — the
integration reloads itself automatically, no restart required.

## Automations: getting notified about new fires

**Simplest option** — watch `sensor.calfire_latest_incident` with a plain
`state` trigger:

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
new fire. If more than one fire appears in the same poll cycle, this
entity surfaces whichever has burned the most acres; the others are still
tracked as their own per-fire entities, just not reflected here.

**Alternative** — trigger on the `calfire_new_incident` event instead,
which fires once for *every* new fire (not just the most recent):

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

## Entity lifecycle

New fires get their own entity automatically as they appear in the feed.
When a fire drops out of the feed (contained/closed and removed by CAL
FIRE), its entity is removed from Home Assistant entirely — but only after
it's been missing from the feed for 2 consecutive polls in a row, so a
brief CAL FIRE API hiccup doesn't delete an entity for a fire that's
actually still burning. At the default 10-minute scan interval, that means
removal can take up to ~20–30 minutes after CAL FIRE actually drops the
fire — not a fixed time, since it depends on exactly when between polls
the fire disappeared. Before that threshold is hit, the entity is marked
`unavailable` rather than removed.

## Troubleshooting

**An attribute is always `null` even though CAL FIRE's own incident page
shows a value.** CAL FIRE's feed is internal/undocumented, so field names
have been reverse-engineered. Enable debug logging to see the raw field
names being sent:

```yaml
logger:
  default: warning
  logs:
    custom_components.calfire: debug
```

After a restart, check the log for a line like `Sample CAL FIRE incident
properties: [...]` — the actual field names in the current feed.

**A fire's entity is still around well beyond the ~30 minute removal
window.** Check, in order:

1. Whether CAL FIRE's own incidents page still lists it — they often keep
   a fire listed for a while after full containment, which isn't a bug on
   our end.
2. Debug logs (same setup as above) for lines like `missing from feed
   (x/y consecutive polls)`, which show the actual countdown in progress.

## Notes

- The feed only includes fires CAL FIRE currently tracks (roughly 10+ acre
  wildfires and other significant incidents), not every reported ignition.
- If you'd rather the sensor's *state* be percent contained instead of
  acres burned, that's a one-line change to `native_value` in `sensor.py`.
