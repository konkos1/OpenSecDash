# ADR-032: Logging & Diagnostics

> **Implementation status (2026-07-09):** Implemented.
> Diagnostics page, plugin/datasource diagnostics, redacted debug reports, logs, and downloadable debug-report ZIP exist.



## Diagnostics Page

Navigation:

```none
Settings
→ Diagnostics
```


---

Display:

### Plugins

for example:

```none
CrowdSec
🟢 Healthy

Traefik
🟢 Healthy

GeoBlock
🔴 Error
```


---

### Data sources

for example:

```none
CrowdSec Log
🟢 OK

Traefik Log
🟢 OK

GeoBlock Log
🔴 Not Found
```


---

### Last import

for example:

```none
CrowdSec

12 seconds ago
```


---

### Event Rate

for example:

```none
CrowdSec
14 Events/min
```


---

### Errors

for example:

```none
last 100 errors
```


---

### Download Support Bundle

Button:

`Create Support Bundle`


---

Creates:

`support.zip`


---

Contains:

```none
System info

Plugin status

Configuration
(without secrets)

Logs
```


---

Then someone can write on GitHub:

```none
OpenSecDash 1.0.0
support.zip attached
```


---
