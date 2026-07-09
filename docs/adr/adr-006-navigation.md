# ADR-006: Navigation

> **Implementation status (2026-07-09):** Partially implemented.
> Dashboard, CrowdSec, Access, Assets, Events, IP Explorer, Settings, Diagnostics, and Rollups exist. Torblock-specific navigation is not implemented as a dedicated plugin/page.



## Dashboard

Minimal.

Shows only:

```none
Number of CrowdSec bans
Number of geoblocks
Number of torblocks
Number of access events
Number of assets
```


---

## CrowdSec

```none
Active bans
History
Top scenarios
Top countries (heatmap)
Top times
```


---

## Access

```none
Logs

Search
Filters
```


---

## Assets

```none
Installed applications

Versions
Updates
```


---

## Events

```none
All event types
```


---

## IP Explorer

```none
Central detail view
```


---

## Settings

```none
Configuration
```


---

---

## Implementation notes (2026-07-09)

The current navigation includes Dashboard, Events, IP Explorer entry points, Assets, Settings, Diagnostics, Rollups, and plugin-provided pages such as CrowdSec and Access. A dedicated Torblock page/plugin is not currently implemented.

