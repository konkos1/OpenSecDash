# ADR-036 Database Schema

> **Implementation status (2026-07-09):** Implemented.
> Current SQLAlchemy models include settings, plugins, datasources, events, insights,
> insight rules, systems, assets, actions, aggregations, diagnostics, CrowdSec decisions,
> permanent ASN policies/exceptions/enforcements, and GeoIP cache.


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
asn_organization
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

## Implementation notes (2026-08-26)

Permanent manual ASN bans add three central tables alongside `crowdsec_decisions`:

```none
crowdsec_asn_bans
crowdsec_asn_ban_exceptions
crowdsec_asn_ban_enforcements
```

`crowdsec_asn_bans` has one unique normalized ASN and stores policy status, current and
previous ASN-organization snapshots, review timestamps/flag, a debounced organization
candidate with observation/IP evidence, last match, and removal error. Its status is
constrained to `active` or `removing`. Events and GeoIP cache rows store
`asn_organization` separately from the IP-specific `isp` value.

`crowdsec_asn_ban_exceptions` belongs to one policy with cascading deletion and has a
unique `(asn_ban_id, ip)` pair. `source_action_id` is a nullable audit correlation value,
not a foreign key, so Action retention cannot invalidate an exception.

`crowdsec_asn_ban_enforcements` also belongs to one policy with cascading deletion and
has a unique `(asn_ban_id, ip)` pair. It retains the exact CrowdSec decision ID, expiry,
scenario, last classified ASN, last event/action correlation values, and ID-specific
release-pending state. `last_event_id` and `action_id` are nullable correlations rather
than foreign keys because Event or Action retention must not remove ownership evidence.
The table is not a second active-decision inventory; `crowdsec_decisions` remains the
synchronized CrowdSec state.
