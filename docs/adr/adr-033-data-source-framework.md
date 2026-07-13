# ADR-033: Data Source Framework

> **Implementation status (2026-07-09):** Partially implemented.
> Datasource lifecycle, plugin collection/tick processing, source health, counters, cursor/backlog concepts, and resilient import behavior exist for current plugins. All planned generic source types are not implemented.



## Goal

All data sources should work according to the same rules.

Not, for example:

```none
CrowdSec does A

Traefik does B

GeoBlock does C
```


---

But:

```none
Datasource
↓
Parser
↓
Events
```

for all sources.


---

# Data source types

V1 should support three types.


---

## Log File

Examples:

```none
Traefik Access Log

Nginx Access Log

Caddy Access Log

GeoBlock Log

TorBlock Log
```


---

Source:

`/var/log/...`


---

## Command

Examples:

`CrowdSec LAPI active decisions`

or:

`docker ps`

or:

`apt list --upgradable`


---

## API

Later, for example:

```none
Proxmox

OPNsense

GitHub

Docker Registry
```


---

# Datasource Lifecycle

Every source has the same flow.

```none
Datasource
↓
Collect
↓
Parse
↓
Normalize
↓
Store
```


---

# Health State

Every source has a status.


---

## Healthy 🟢

for example:

`Log readable`


---

## Warning 🟡

for example:

`No new data`


---

## Error 🔴

for example:

`File missing`


---

## Disabled ⚪

for example:

`Disabled by user`


---

# Status display

Diagnostics example:

```none
Datasource         Status

Traefik            🟢 Healthy
CrowdSec           🟢 Healthy
GeoBlock           🔴 Error
```


---

# Check Interval

Configurable per source.


---

Examples:

```none
CrowdSec
10 seconds

Traefik
2 seconds

Asset Inventory
1 hour
```


---

# Cursor System

**Very important.**


---

Example:

`traefik.log`

OpenSecDash stores:

`last position`

This means the entire file is not constantly reread.

Similar to:

`tail -f`


---

# Rotation Support

**Mandatory for V1.**


---

Example:

```none
access.log
↓
access.log.1
↓
new access.log
```


---

OpenSecDash must **detect** this.

Otherwise events are lost.


---

# Parser separation

Not:

```none
Traefik Plugin
↓
directly database
```

But:

```none
Datasource
↓
Parser
↓
Event
```

This allows multiple sources to use the same parser later.


---

Example:

```none
Nginx JSON

Traefik JSON
```

could use the same access parser.


---

# Preview mode

In the settings.


---

Example:

```none
Datasource
→ Test
```

shows:

`last 20 lines`

and:

`which events are created from them`

This saves enormous time during troubleshooting.


---

# Event Counter

Store per source.


---

Examples:

```none
Traefik

1,245,887 events processed
```

or:

```none
CrowdSec

18,421 events processed
```

Useful for Diagnostics.


---

# Error handling

**Invalid lines must never stop the import.**


---

Example:

```none
10,000 valid lines

1 broken line
```

Result:

```none
import 10,000 events

log 1 error
```

Not:

`Import aborted`


---

# Event Deduplication

Optional.


---

Example:

`CrowdSec Import`

provides the same decision multiple times.

OpenSecDash can detect:

`already exists`

and ignore it.


---

# Standard datasources V1

## CrowdSec

* CrowdSec LAPI
* crowdsec.log


---

## Access Logs

* Traefik


---

## GeoBlock

* generic log parser
* PascalMinder/geoblock (is a Traefik plugin on GitHub)


---

## TorBlock

* generic log parser
* PaulLeRoux142/TorBlockRedirect (is a Traefik plugin on GitHub)


---

## Assets

* Docker
* GitHub Releases


---

# Result

This defines how practically every data source in OpenSecDash works.


---

---

## Implementation notes (2026-07-09)

The current implementation uses plugin collection and periodic ticks through the plugin manager. Datasource state, health, counters, backlog flags, resilient event storage, and deduplication exist.

The implemented source behavior is concrete-plugin based. A fully generic UI for all planned source types is still planned.
