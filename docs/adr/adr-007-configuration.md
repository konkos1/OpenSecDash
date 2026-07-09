# ADR-007: Configuration

> **Implementation status (2026-07-09):** Implemented.
> Settings are stored in SQLite and rendered through the UI. Plugin metadata/settings drive plugin configuration, including enabled/visible_if behavior.



No YAML files.

No JSON files.

All settings are stored in SQLite.

The GUI manages everything.

Plugin settings are rendered automatically from the plugin metadata/API.
If a plugin has an `enabled` setting, the following applies:

- `enabled` always remains visible.
- All other settings of this plugin are read-only and are only writeable if `enabled=true`.
- Plugins may additionally define their own `visible_if` rules; these take precedence.


---

Examples:

```none
CrowdSec active
Path to cscli

Traefik log

Geoblock log
```


---
