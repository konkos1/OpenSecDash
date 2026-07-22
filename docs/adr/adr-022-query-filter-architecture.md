# ADR-022: Query & Filter Architecture

> **Implementation status (2026-07-12):** Implemented.
> Events and Access use the shared filter engine with structured operator keys, ASN/hostname/asset filters, time-range presets, URL filters, local-IP filtering, search expressions, saved views, and table column settings. The global header search routes IPs/CIDRs to IP Explorer, matching asset names/hostnames to Asset Explorer, and all other input to Events.
> Update (2026-07-22): Events, Access, and the Events list API use a bounded 24-hour
> default; `range=all` is the explicit all-time choice and saved views preserve it.
> Search expressions have server-side length, token, nesting, and syntax limits.
> Structured fields remain the default search surface, while JSON and raw payloads
> require an explicit, saved-view-compatible opt-in. Exact IP, ASN, status, and country
> terms use bound structured predicates.

> **Intentional scope:** Filter operators are explicit structured keys, not a free-text DSL, so validation and SQLAlchemy-bound queries remain straightforward and safe. Custom time ranges use `range=custom` with `from`/`to` URL parameters, and only the selected time range is preserved between Events and Access. Saved views with the same name in the same scope are overwritten; plugin-provided views are supported as read-only defaults. Torblock is not applicable because no Torblock plugin exists. Generated drill-down values link to matching Events/Access views where a meaningful filter exists; technical, inventory, notification, current-decision, and Rollup values remain intentionally non-drill-down.



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

## Implementation notes (2026-07-12)

The shared `apply_event_filters` engine supports additive structured keys for country lists and exclusions, inclusive status-code bounds, ASN, hostname, and asset. ASN equality is indexed. Events and Access expose the same applicable URL filters and time-range presets (`1h`, `24h`, `7d`, `30d`, and custom `from`/`to` URLs); the selected range is retained when switching between those pages.

Saved views are validated against the filter allowlist. With internal authentication
enabled, they belong to the signed-in user; without it, they remain global to the
instance. They are available independently for Events and Access, preserve supported
route state, replace an existing view with the same name in the same scope, and can be
complemented by read-only plugin-provided defaults. Dashboard and CrowdSec drill-downs
link generated event aggregates to matching filtered views; values without an
equivalent Events/Access query are documented as intentional exceptions above.
