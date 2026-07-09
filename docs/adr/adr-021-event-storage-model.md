# ADR-021: Event Storage Model

> **Implementation status (2026-07-09):** Implemented.
> Events use integer primary keys, structured columns, raw_data, data_json, created_at/event_time, retention_class, indexes, deduplication, rollups, and insight generation.



## Goal

OpenSecDash should be able to:

* store millions of events
* filter quickly
* keep IP Explorer performant
* keep Asset Explorer performant
* calculate rollups efficiently

**without** already needing a complex database such as PostgreSQL.


---

# Principle

Events are stored in two tracks:

## Structured fields

For search, filters, and correlation.


---

## Raw data

For later traceability.


---

Example:

```json
{
  "event_type": "access.error",
  "ip": "1.2.3.4",
  "status_code": 404,
  "path": "/wp-login.php"
}
```

plus:

`Original log line`


---

# Event table

`events`


---

## Required fields

```sql
id
timestamp
plugin_id
source_id
event_type
```


---

## Correlation fields

```sql
ip
asset_id
country
asn
hostname
```


---

## Access fields

```sql
method
path
status_code
```


---

## Metadata

`severity`

Values:

```sql
info
warning
error
critical
```


---

## Payload

`data_json`

contains for example:

```json
{
  "crowdsec_scenario": "...",
  "ban_duration": "...",
  "user_agent": "...",
  ...
}
```


---

## Raw data

`raw_data`

Example:

`time="..." level=info msg="..."`


---

# Event ID

Do not use UUID.

SQLite works excellently with:

`INTEGER PRIMARY KEY`

Therefore:

`id INTEGER PRIMARY KEY`

Advantages:

* smaller
* faster
* less storage


---

# Event time

**Important**:

Not only:

`timestamp`

but:

`created_at`

and

`event_time`


---

Example:

`Log was read 2 minutes late`


---

Then:

```none
event_time
=
time of the event

created_at
=
time of storage
```


---

# Searchability

Indexes directly in V1.


---

## By time

`event_time`


---

## By IP

`ip`


---

## By asset

`asset_id`


---

## By event type

`event_type`


---

## Combination

`(event_type, event_time)`


---

This probably covers 80% of all queries.


---

# Event deletion

Retention never runs:

`DELETE`

per event.


---

But:

```sql
DELETE
WHERE event_time < ...
```

in blocks.


---

# Event retention marker

Practical for the UI:

`retention_class`


---

Example:

```none
raw
daily
monthly
```


---

This allows the same UI to later display both raw data and rollups.


---

# Event attachments

Not in V1.


---

So no:

* PCAP
* Screenshots
* Files


---

# Why structured fields matter

Take the IP Explorer.

If everything is in JSON:

`data_json`

SQLite constantly has to parse JSON.


---

With:

```sql
ip
country
status_code
```

search works directly via indexes.


---

Therefore:

## Frequently used fields

always as separate columns.


---

## Plugin-specific fields

in:

`data_json`


---

# Example CrowdSec Event

```json
{
  "event_type": "security.ban",
  "ip": "1.2.3.4",
  "country": "RU",
  "data_json": {
    "scenario": "crowdsecurity/http-probing",
    "duration": "4h"
  }
}
```


---

# Example Access Event

```json
{
  "event_type": "access.error",
  "ip": "1.2.3.4",
  "status_code": 404,
  "path": "/wp-login.php"
}
```


---
