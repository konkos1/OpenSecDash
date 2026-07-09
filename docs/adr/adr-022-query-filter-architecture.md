# ADR-022: Query & Filter Architecture

> **Implementation status (2026-07-09):** Partially implemented.
> Events/access pages support practical filters, search expressions, URL filters, local-IP filtering, and table column settings. Saved views/global search are not fully implemented.



## Goal

All pages use the same filter engine.

Not:

`CrowdSec Filter`

and separately:

`Access Filter`

and separately:

`IP Explorer Filter`

But:

```none
One filter engine
Many views
```


---

# Global filters

Every event-based page supports the same basic filters.

## Time range

```none
Last hour
Today
24 hours
7 days
30 days
Custom
```


---

## Event type

```none
access.*
security.*
asset.*
system.*
```


---

## Source

Example:

```none
Traefik
CrowdSec
Geoblock
Torblock
```


---

## Asset

Example:

```none
Nextcloud
Home Assistant
Immich
```


---

## IP

`1.2.3.4`


---

# Advanced filters

## Status code

```none
200
301
403
404
500
```


---

## Country

```none
DE
US
RU
CN
...
```


---

## ASN

```none
AS3320
AS12389
...
```


---

## Hostname

`crawl.example.com`


---

## Path

```none
/wp-login.php
/admin
/xmlrpc.php
```


---

# Filter model

Internally, all filters are built the same way.

Example:

```json
{
  "event_type": "security.*",
  "country": "RU",
  "since": "24h"
}
```

This lets plugins use the same engine.


---

# Filter operators

V1 should already be able to do more than:

`=`


---

## Equals

`country = RU`


---

## Not equals

`country != DE`


---

## Contains

`path contains wp-login`


---

## List

`country IN (RU,CN,US)`


---

## Range

`status_code >= 400`


---

# Global search

There should be exactly one search bar.

Example:

`1.2.3.4`

opens directly:

`IP Explorer`


---

Example:

`Nextcloud`

opens:

`Asset Explorer`


---

Example:

`/wp-login.php`

shows matching events.


---

# Saved views

Example:

```none
My views

✓ CrowdSec today

✓ All 404

✓ Russian accesses

✓ WordPress Scanner
```


---

Internally:

```json
{
  "name": "WordPress Scanner",
  "filter": {
    "path": "/wp-login.php"
  }
}
```


---

# Default views

Plugins may provide views.

CrowdSec:

```none
Active bans

Top scenarios

Latest bans
```


---

Access:

```none
404 errors

500 errors

Top URLs
```


---

# URL-based filters

Important for links.

Example:

`/events?ip=1.2.3.4`


---

or:

`/events?country=RU`


---

or:

`/events?asset=nextcloud`


---

This makes all tables clickable.


---

Example:

```none
Top countries

RU (152)
↓
Click
↓
All events from RU
```


---

# Drill-down principle

Fixed UX principle:

Every generated number should be clickable.

Example:

```none
404 Errors

182
↓
Click
↓
shows:
182 concrete events
```


---

```none
CrowdSec Bans

12
↓
Click
↓
shows:
12 concrete bans
```


---

# Filter state

When switching between pages, the filter is preserved.

Example:

```none
Time range:
Last 24h
```

Switch:

```none
Dashboard
↓
CrowdSec
↓
Events
```

Time range remains:

`Last 24h`


---

---

## Implementation notes (2026-07-09)

The Events and Access views implement practical filters and search, including event type, IP, country, status code, path, plugin/source, local-IP behavior, boolean-style text search, URL-based filters, and configurable table columns.

Saved views and a single global search bar that routes directly to IP/Asset Explorer remain planned.

