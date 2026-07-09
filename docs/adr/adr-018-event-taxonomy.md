# ADR-018: Event Taxonomy

> **Implementation status (2026-07-09):** Implemented.
> Events use hierarchical event_type values such as `access.*`, `security.*`, `asset.*`, `action.*`, and system/plugin events.



## Goal

All events in OpenSecDash should follow a fixed, hierarchical structure.

This makes:

* Filters easier
* Plugins easier
* Insights easier
* API more stable
* Database more consistent


---

# Basic principle

Not:

```none
BAN
GEOBLOCK
TORBLOCK
```

but:

```none
security.ban
security.geoblock
security.torblock
```


---

Not:

```none
ACCESS
```

but:

```none
access.allowed
access.denied
access.error
```


---

# Event categories V1

## Access

```none
access.allowed
access.denied
access.error
```


---

Examples:

```none
200
↓
access.allowed
```


---

```none
404 
↓ 
access.error
```


---

```none
403
↓
access.denied
```


---

# Security

```none
security.ban
security.unban
security.geoblock
security.torblock
```


---

Later, for example:

```none
security.fail2ban
security.waf
security.ratelimit
```


---

# Assets

```none
asset.created
asset.updated
asset.removed
```


---

Example:

```none
New app detected
↓
asset.created
```


---

# System

```none
system.startup
system.shutdown
system.error
system.warning
```


---

# Plugin

```none
plugin.loaded
plugin.unloaded
plugin.error
```


---

# Actions

```none
action.executed
action.failed
```


---

Example:

```none
CrowdSec Ban successful 
↓ 
action.executed
```


---

# Why this matters

Then filtering can use:

`security.*`

shows:

```none
security.ban
security.unban
security.geoblock
security.torblock
```


---

Or:

`access.*`

shows:

```none
access.allowed
access.denied
access.error
```


---

# Database

Event table stores:

```json
{
  "event_type": "security.geoblock"
}
```

Not:

```json
{
  "event_type": "GEOBLOCK"
}
```


---

# Insight system

Then rules can say, for example:

`security.*`

instead of:

```none
security.ban
security.unban
security.geoblock
security.torblock
...
```


---
