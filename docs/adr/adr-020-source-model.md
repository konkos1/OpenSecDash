# ADR-020: Source Model

> **Implementation status (2026-07-09):** Partially implemented.
> Datasource records, source health, event counters, and plugin/source fields exist. The generic source UI/framework is implemented around current plugins rather than every planned source type.



## Goal

OpenSecDash should distinguish between:

`Plugin`

and

`Data source (Source)`


---

# Why?

A plugin describes:

`How data is processed`

A source describes:

`Where the data comes from`


---

## Example

Plugin:

`Traefik`


---

Possible sources:

```none
Traefik Access Log
Traefik Error Log
Traefik Audit Log
```


---

Or:

Plugin:

`CrowdSec`


---

Possible sources:

```none
CrowdSec Decisions
CrowdSec Log
CrowdSec Metrics
```


---

# Source model

`Source`

Fields:

```python
id
name
plugin_id
enabled
source_type
config
```


---

Example:

```json
{
  "name": "Traefik Access Log",
  "plugin_id": "traefik",
  "enabled": true,
  "source_type": "logfile"
}
```


---

# Source types V1

## logfile

Example:

`/var/log/traefik/access.log`


---

## command

Example:

`cscli decisions list`


---

## http

Example:

`assets.json`

via HTTP.


---

## file

Example:

`local JSON file`


---

Later, for example:

```none
database
syslog
webhook
mqtt
```


---

# Multiple sources per plugin

**Very important.**

Not:

```none
Traefik Plugin
=
one source
```

but:

```none
Traefik Plugin
=
0..n Sources
```


---

Example:

```none
Traefik Plugin

Source 1:
Access Log

Source 2:
Error Log

Source 3:
Audit Log
```


---

# Configuration page

Then OpenSecDash could show:

```none
Sources

✓ CrowdSec Decisions
✓ CrowdSec Log
✓ Traefik Access Log
✗ Traefik Error Log
✗ Nginx Access Log
```


---

# Event origin

Every event stores:

```python
plugin
source
```


---

Example:

```json
{
  "plugin": "traefik",
  "source": "access-log"
}
```


---

This allows later filtering:

```none
All events from:
Traefik Access Log
```


---

# Source Health

Plan directly in V1:

Each source has status.

```none
🟢 OK

🟡 Warning

🔴 Error
```


---

Examples:

```none
🟢 Traefik log found
```


---

```none
🔴 CrowdSec cscli missing
```


---

```none
🟡 HTTP endpoint timeout
```


---

# Source Metrics

Very helpful later.

Per source:

```none
Last event

Events today

Errors today

Status
```


---

Example:

```none
Traefik Access Log

Events:
128,432

Last event:
3 seconds ago
```


---

# Result

This creates a clean separation:

```none
Plugin
↓
Source
↓
Event
↓
Insight
```


---

---

## Implementation notes (2026-07-09)

`Datasource` records are implemented with plugin id, source type, JSON config, enabled flag, status, last run/event timestamps, last error, event counters, and backlog state.

The current implementation is driven by concrete plugins and periodic collection hooks. The model supports multiple sources per plugin, but not every planned generic source type is exposed as a generic user-facing source builder.

