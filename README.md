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
admin unit, incident type, percent contained, start/update timestamps, a
computed `days_burning` (days since the fire started, parsed from CAL
FIRE's start date), source URL, latitude/longitude, which `hub` it came
from (only meaningful if you run more than one instance — see
Configuration below), and distance from your configured center point as
both `distance_km` and `distance_mi` (always both present, regardless of
which unit you used when setting the radius — see Configuration below).
Because latitude/longitude are exposed as attributes, these entities also
show up on the built-in Lovelace **Map** card.

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

- **Name**: shown in Settings → Devices & Services, and exposed as a
  `hub` attribute on every fire entity. Only really matters if you're
  running more than one instance of this integration (see "Multiple
  hubs" below) — otherwise the default is fine.
- **Distance unit (km / mi)**: which unit you're about to enter the
  radius in below — it doesn't affect anything else. Every fire's
  distance is always exposed as both `distance_km` and `distance_mi`
  attributes no matter what you pick here.
- **Radius**: only show fires within this distance of the center point,
  in whichever unit you picked above. Leave at `0` for all active
  incidents statewide.
- **Scan interval (minutes)**: how often to poll the feed. CAL FIRE
  doesn't update the underlying data much faster than every 15–30 minutes
  during an active incident, so the default of 10 minutes is reasonable —
  you don't need to go much lower.
- **Center latitude / longitude**: leave both blank to use your Home
  Assistant instance's configured home location (Settings → System →
  General) as the center for the radius filter and each fire's distance
  attributes. Set both to center on somewhere else instead — useful if
  your HA server isn't physically where you actually want "nearby"
  measured from (a vacation property, a family member's house, etc).

### Changing settings later

All of the above can be changed after setup without removing the
integration: go to **Settings → Devices & Services**, find "CAL FIRE
Incidents", and click **Configure**. Changes take effect immediately — the
integration reloads itself automatically, no restart required.

One thing to know: if you switch **Distance unit** later, the **Radius**
number itself doesn't get converted for you — it's just interpreted in
the new unit going forward, so re-enter it if you want the same real-world
distance (e.g. a radius of `50` meant 50 km before switching to miles;
after switching, that same `50` now means 50 miles, roughly 1.6× larger).

### Multiple hubs

You can add this integration more than once — e.g. one instance centered
on your home, another centered on a relative's place — each with its own
radius, center point, and name. Just go through **Settings → Devices &
Services → Add Integration → CAL FIRE Incidents** again and give the
second one a distinct **Name** (e.g. "CAL FIRE — In-Laws") so the two are
easy to tell apart in the UI.

Every fire entity carries a `hub` attribute set to that instance's Name,
which is the reliable way to filter a dashboard down to just one hub's
fires — see the `attributes: {hub: "..."}` filter in the dashboard
examples below. Fire entities themselves are scoped per hub internally, so
the same real-world fire matching both hubs' radii (overlapping areas, or
either radius left at `0`/statewide) correctly gets its own entity under
each hub rather than colliding.

One thing to watch for: each hub's `Latest Incident` singleton is named
identically ("Latest Incident"), so Home Assistant will auto-generate
`sensor.latest_incident` for the first hub and `sensor.latest_incident_2`
for the second (entity_id doesn't otherwise indicate which hub it belongs
to). Check each one's `hub` attribute in Developer Tools → States to
confirm which is which before wiring up automations — see the note in the
automations section below on scoping a single automation to one hub.

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

Available fields on `trigger.event.data`: `unique_id`, `hub`, `name`, `county`,
`admin_unit`, `incident_type`, `acres_burned`, `percent_contained`,
`days_burning`, `distance_km`, `distance_mi`, `url`, `latitude`, `longitude`.

**Running multiple hubs and only want one to trigger this automation?**
Both examples above fire for whichever hub the entity_id/event belongs to
by nature (each hub has its own `sensor.calfire_latest_incident_N` and its
own `calfire_new_incident` events) — but if you'd rather have one
automation watch a specific hub regardless of entity_id numbering, add a
condition checking the `hub` attribute/field, e.g.
`{{ trigger.to_state.attributes.hub == 'CAL FIRE Incidents' }}` (state
trigger) or `{{ trigger.event.data.hub == 'CAL FIRE Incidents' }}`
(event trigger), matching whatever Name you gave that hub.

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

**Upgrading from a version before multi-hub support.** As of 0.10.0,
per-fire entities are internally scoped per hub instance (to prevent two
hubs colliding over the same fire). If you already had this integration
set up, its existing entities are migrated automatically the next time it
reloads or Home Assistant restarts — same entity_ids, same history,
nothing to do on your end. This is a one-time, one-way migration; there's
no need to remove and re-add the integration.

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

**`days_burning` is always `null`.** This is computed by parsing the raw
`started` date CAL FIRE provides, which — like everything else about this
feed — isn't from a documented, stable format. If parsing fails, debug
logs (same setup as above) show a line like `Could not parse started date
'...'`, which will tell you exactly what format is actually coming
through so the parser can be adjusted.

**A fire shows up in Settings → Entities but not on a dashboard card
(particularly the auto-entities/Mushroom one).** Two known causes:
1. Fixed as of 0.9.1 — a fire briefly missing from the feed (within its
   removal grace period) used to have all its attributes wiped to
   nothing, including `distance_km`, which broke dashboard cards that sort
   or filter on that attribute. Update to the latest version if you still
   see this.
2. If the fire is brand new, the Mushroom/auto-entities card example
   below filters on a live state attribute specifically to avoid this, but
   if you've customized it to filter using auto-entities' `integration:`,
   `device:`, or `area:` filter types instead, those read from the entity
   registry rather than live state and can lag behind a just-created
   entity until the dashboard is refreshed. A hard refresh
   (Ctrl+Shift+R) confirms this is the cause.

## Dashboard: list of active fires by distance

Since fire entities are created and removed dynamically, a normal
Lovelace **Entities** card (which needs a fixed list of entity IDs written
into its config) won't stay up to date on its own. Instead, use a
**Markdown card** with a small template that looks up all current
`calfire` entities at render time and sorts them by `distance_km`:

```yaml
type: markdown
title: Active CAL FIRE Incidents
content: >
  {% set entity_ids = integration_entities('calfire')
       | reject('eq', 'sensor.calfire_latest_incident') | list %}
  {% set ns = namespace(rows=[]) %}
  {% for eid in entity_ids %}
    {% set st = states[eid] %}
    {% if st is not none and st.state not in ['unavailable', 'unknown']
          and st.attributes.distance_km is not none %}
      {% set ns.rows = ns.rows + [st] %}
    {% endif %}
  {% endfor %}
  {% set sorted_rows = ns.rows | sort(attribute='attributes.distance_km') %}
  {% if sorted_rows | length == 0 %}
  No active fires currently tracked.
  {% else %}
  | Fire | Distance | Acres | Contained | Days | County |
  | --- | --- | --- | --- | --- | --- |
  {% for st in sorted_rows %}
  | [{{ st.name }}]({{ st.attributes.url }}) | {{ st.attributes.distance_mi }} mi | {{ st.state }} ac | {{ st.attributes.percent_contained }}% | {{ st.attributes.days_burning }} | {{ st.attributes.county }} |
  {% endfor %}
  {% endif %}
```

Add it via **Edit Dashboard → Add Card → Manual**, paste the YAML above,
and save. It updates automatically as fires appear, disappear, and move
in distance-sorted order — no manual entity list to maintain. Prefer
kilometers? Swap `distance_mi` / `mi` for `distance_km` / `km`.

Running multiple hubs and want this table scoped to just one? Add a
`hub` check to the `{% if %}` condition, e.g.
`and st.attributes.hub == 'CAL FIRE Incidents'` (matching whatever Name
you gave that hub) — `integration_entities('calfire')` returns entities
from every hub instance mixed together, so this is how to split them back
apart per table.

### One Mushroom card per fire

If you're already using [Mushroom cards](https://github.com/piitaya/lovelace-mushroom)
and want a proper card per fire (rather than a markdown table), add the
[auto-entities](https://github.com/thomasloven/lovelace-auto-entities)
custom card too (both via HACS → Frontend). `auto-entities` can generate a
full card per matched entity — using `card_param: cards` — rather than
just filling in a list, which is what makes this possible without
manually adding/removing a card every time a fire starts or closes out.

Making the card *tappable to open that fire's CAL FIRE page* needs one
more custom card:
[config-template-card](https://github.com/iantrich/config-template-card)
(HACS → Frontend). Here's why it's necessary: `auto-entities` only
performs one narrow substitution — the literal string `this.entity_id`,
wherever a field's value is exactly that, gets replaced with the matched
entity_id. It doesn't understand anything like `this.attributes.url`.
Mushroom's own live Jinja templating (used above for `primary`,
`secondary`, `icon_color`, etc.) doesn't extend to `tap_action` either —
that's a fixed config, not something Mushroom re-templates per entity.
`config-template-card` closes that gap: it evaluates JS template
expressions (`${ ... }`) anywhere in a nested card's config, including
inside `tap_action`, using the real entity_id that `auto-entities` already
substituted in:

```yaml
type: vertical-stack
cards:
  - type: custom:mushroom-title-card
    title: Active Fires
    subtitle: Sorted by distance from center point
  - type: custom:auto-entities
    show_empty: true
    card:
      type: grid
      columns: 1
      square: false
    card_param: cards
    filter:
      include:
        - attributes:
            unit_of_measurement: acres
          options:
            type: custom:config-template-card
            entities:
              - this.entity_id
            card:
              type: custom:mushroom-template-card
              entity: this.entity_id
              primary: "{{ state_attr(config.entity, 'friendly_name') }}"
              secondary: >-
                {{ states(config.entity) }} ac •
                {{ state_attr(config.entity, 'percent_contained') | round(0) }}% contained •
                {{ state_attr(config.entity, 'days_burning') }}d •
                {{ state_attr(config.entity, 'distance_mi') }} mi •
                {{ state_attr(config.entity, 'county') }}
              icon: mdi:fire
              icon_color: >-
                {% set pc = state_attr(config.entity, 'percent_contained') | float(0) %}
                {% set pc = [[pc, 0] | max, 100] | min %}
                hsl({{ (pc * 1.2) | round(0) }}, 70%, 45%)
              tap_action:
                action: url
                url_path: ${ states[this._config.entities[0]].attributes.url }
    sort:
      method: attribute
      attribute: distance_km
      numeric: true
```

How the entity actually gets to `tap_action` here, in order:
1. `auto-entities` matches each fire and replaces the literal value
   `this.entity_id` wherever it appears — including inside
   `config-template-card`'s own `entities:` list — with that fire's real
   entity_id.
2. `config-template-card` then has a concrete entity_id baked into its own
   config at `this._config.entities[0]`, and evaluates the `${ ... }` JS
   expression in `tap_action.url_path` using it — `states[...]` here is
   `config-template-card`'s own JS state lookup, not Home Assistant's
   Jinja `states()` function used elsewhere in this card.
3. Everything inside the nested `mushroom-template-card` (`primary`,
   `secondary`, `icon_color`) still uses ordinary Jinja via
   `config.entity`, exactly as before — that part didn't need to change.

If adding a third custom card just for tap-to-open feels like too much,
the Markdown card option above already handles per-fire links correctly
with zero extra dependencies (Markdown's own link rendering resolves
templated URLs fine, unlike Mushroom's `tap_action`).

A couple of other pieces worth knowing:
- `filter.include: [{attributes: {unit_of_measurement: acres}}]` picks up
  every per-fire entity automatically — every `CalFireIncidentSensor` has
  this, and the `Latest Incident` singleton doesn't, so nothing extra is
  needed to exclude it. This deliberately matches on a live *state*
  attribute rather than using auto-entities' `integration:` filter type
  (which matches via the entity/device *registry* instead of live state).
  That distinction matters: registry-based lookups can lag behind
  brand-new entities until the dashboard is manually refreshed, whereas
  state-based filters like this one update instantly the moment an
  entity's state appears or disappears — no manual refresh ever needed,
  in either direction.
- **Running multiple hubs?** Add a second `attributes` condition to the
  same include filter to scope the card to just one hub, e.g.:
  ```yaml
  include:
    - attributes:
        unit_of_measurement: acres
        hub: "CAL FIRE Incidents"
      options:
        ...
  ```
  (all conditions inside one filter entry must match — this is an AND,
  not an OR). Duplicate the whole card, swap in each hub's Name, and
  you'll get a separate "Active Fires" section per location.
- `sort` orders the generated cards nearest-first by `distance_km`, same
  as the Markdown table above (sorting stays on `distance_km` regardless
  of what's displayed in `secondary` — it's just a stable numeric key).
  The `secondary` text itself shows `distance_mi`; swap in `distance_km`
  there if you'd rather see kilometers.
- Icon color is a continuous red → yellow → green gradient as containment
  goes from 0% to 100%, computed as an HSL hue (`hsl(hue, 70%, 45%)`,
  where hue runs from 0° at 0% contained to 120° at 100%) rather than
  fixed color buckets. Tweak the `70%`/`45%` saturation/lightness values,
  the `1.2` multiplier (hue range), or swap in different Mushroom card
  fields (e.g. `secondary` wording, adding a `badge_icon` for
  `is_active`, etc).

## Notes

- The feed only includes fires CAL FIRE currently tracks (roughly 10+ acre
  wildfires and other significant incidents), not every reported ignition.
- If you'd rather the sensor's *state* be percent contained instead of
  acres burned, that's a one-line change to `native_value` in `sensor.py`.
