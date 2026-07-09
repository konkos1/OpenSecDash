# ADR-002: Architecture

> **Implementation status (2026-07-09):** Implemented.
> The code uses centralized event ingestion and storage before UI rendering. Plugins emit normalized events via the core event pipeline.



## Event-centered

Not:

`Traefik -> UI`

Not:

`CrowdSec -> UI`

But:

```none
Source
↓
Parser
↓
Event
↓
Database
↓
UI
```


---

## Event types

V1:

```python
ACCESS
BAN
UNBAN
GEOBLOCK
TORBLOCK
APP
SYSTEM
```

Later, for example:

```python
FAIL2BAN
PFSENSE
OPNSENSE
ADGUARD
PIHOLE
```


---
