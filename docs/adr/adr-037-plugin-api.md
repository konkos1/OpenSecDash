# ADR-037: Plugin API

> **Implementation status (2026-07-09):** Implemented.
> Plugin API v2 is implemented with package layout, PluginMetadata, settings, lifecycle hooks, capabilities, PluginContext, web hooks, IP panel hooks, dedupe hooks, and action/export hooks.



## Goal

All plugins should use the same interface.

Not:

```none
crowdsec.py
does something

traefik.py
does something else
```

But:

`class Plugin`

as a common base.


---

# Plugin Metadata

Every plugin provides:

```none
id
name
version
description
author
```

Example:

```none
id = "crowdsec"
name = "CrowdSec"
version = "1.0.0"
```


---

# Plugin categories

We had defined:

```none
datasource
enrichment
action
export
```


---

A plugin can have multiple categories.

Example:

```none
CrowdSec

Datasource
Action
```


---

# Base interface

```python
class Plugin:
    id: str
    name: str
    version: str

    async def startup(self):
        pass

    async def shutdown(self):
        pass
```


---

# Datasource Plugin

Example:

```python
class DataSourcePlugin(Plugin):

    async def collect(self):
        pass
```


---

Return value:

`list[Event]`


---

Example:

```none
CrowdSec
↓
BAN Event
```


---

# Action Plugin

```python
class ActionPlugin(Plugin):

    async def execute(
        self,
        action
    ):
        pass
```


---

Example:

```none
Ban IP
↓
cscli decisions add
```


---

# Export Plugin

```python
class ExportPlugin(Plugin):

    async def export(
        self,
        event
    ):
        pass
```


---

Example:

```none
MQTT
↓
Publish Topic
```


---

# Enrichment Plugin

```python
class EnrichmentPlugin(Plugin):

    async def enrich(
        self,
        event
    ):
        pass
```


---

Example:

```none
IP
↓
Country
ASN
```


---

# Plugin Settings

Every plugin defines its own fields.

Example:

```json
{
  "host": "...",
  "port": 1883
}
```


---

The GUI automatically renders the settings page from this.


---

# Health Status

Every plugin provides:

`async def health():`


---

Result:

```json
{
  "status": "healthy"
}
```


---

or:

```json
{
  "status": "error",
  "message": "Log file not found"
}
```


---

# Diagnostics Integration

This lets Diagnostics automatically display, for example:

```none
CrowdSec      Healthy
MQTT          Healthy
GeoBlock      Error
```


---

# Plugin Discovery

Directory, for example:

```none
plugins/

crowdsec/
mqtt/
traefik/
```

At startup:

```none
Plugin Manager
↓
Load plugins
↓
Register
```


---

# Plugin Versioning

Important for OSS.

Every plugin:

`api_version = "1"`


---

This lets us later:

`OpenSecDash 2.x`

develop without immediately breaking old plugins.


---
