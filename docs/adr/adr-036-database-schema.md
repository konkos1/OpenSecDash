# ADR-036 Database Schema

> **Implementation status (2026-07-09):** Implemented.
> Current SQLAlchemy models include settings, plugins, datasources, events, insights, insight_rules, systems, assets, actions, aggregations, diagnostics, CrowdSec decisions, and GeoIP cache.


## Tables

```none
settings
plugins
datasources

events
insights

systems
assets

actions

aggregations_daily
aggregations_monthly

diagnostics
```


---

## settings

```none
id
key
value
updated_at
```

Examples:

```none
language = de
domain = mydomain.de
live_default = true
retention_days = 30
```


---

## events

The most important table.

```none
id
timestamp

source
plugin

event_type

ip
country
asn

hostname

severity

data_json
```

Example:

```none
BAN
GEOBLOCK
TORBLOCK
ACCESS
APP
SYSTEM
```


---

## insights

Result of the Correlation Engine.

```none
id
timestamp

type
confidence

title
description

related_event_ids
```


---

## systems

From `assets.json`.

```none
id

vmid
hostname
type

last_seen
```


---

Example:

```none
100
traefik
lxc
```


---

## assets

Apps inside a system.

```none
id

system_id

is_active
last_seen

name
version

release_url

latest_version

update_available

last_checked
```


---

Example:

```none
Traefik

v3.7.5

v3.8.0

true
```


---

## actions

For ban/unban etc.

```none
id

timestamp

action_type

target

status

result
```


---

## aggregations_daily

For heatmaps etc.

```none
date

metric

key

value
```


---

Examples:

```none
2026-06-20

geoblock_country

RU

1287
```


---

or:

```none
2026-06-20

ban_scenario

http-probing

84
```


---

## aggregations_monthly

Same structure.

Only:

`2026-06`

instead of day.


---

## diagnostics

Plugin status.

```none
plugin

status

last_run

last_error
```


---

---

## Implementation notes (2026-07-09)

The current SQLAlchemy model set contains:

```none
settings
plugins
datasources
events
insights
insight_rules
systems
assets
actions
aggregations_daily
aggregations_monthly
diagnostics
crowdsec_decisions
geoip_cache
```

The `events` table has been expanded beyond the original sketch and includes:

```none
id
timestamp
created_at
event_time
source
source_id
plugin
plugin_id
event_type
severity
ip
country
city
asn
isp
hostname
asset_id
method
path
status_code
data_json
raw_data
retention_class
geoip_checked
is_local_ip
```

The `assets` table includes source identity, host URLs, release API/web URLs, update status, MQTT publish flag, and last checked timestamps.

