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

## Implementation notes (2026-08-26)

Permanent manual ASN policies extend the security taxonomy with:

```none
security.asn_ban.enabled
security.asn_ban.disabled
security.asn_ban.exception.added
security.asn_ban.exception.removed
security.asn_ban.provider_changed
security.asn_ban.provider_change.acknowledged
security.ban.asn_policy
security.unban.asn_policy_reclassified
```

The `security.asn_ban.*` events describe local policy lifecycle and review state.
The stable `provider_changed` event name is retained for compatibility, but the compared
value is the ASN organization rather than the IP-specific ISP/company label.
`security.ban.asn_policy` records one successful seven-day IP decision caused by a
policy and therefore participates in the existing `security.ban*` counters.
`security.unban.asn_policy_reclassified` records the exact policy-owned decision release
after a later GeoIP classification. Failed actions continue to use `action.failed`.


---
