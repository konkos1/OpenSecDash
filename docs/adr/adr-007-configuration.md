# ADR-007: Configuration

> **Implementation status (2026-08-01):** Implemented.
> Settings are stored in SQLite and rendered through the UI. Plugin metadata/settings drive plugin configuration, including enabled/visible_if behavior. Select options may declare value-specific information that is rendered below the field for the currently selected value. A setting may declare several conditions at once, and password settings are never rendered back.



No YAML files.

No JSON files.

All settings are stored in SQLite.

The GUI manages everything.

Plugin settings are rendered automatically from the plugin metadata/API.
If a plugin has an `enabled` setting, the following applies:

- `enabled` always remains visible.
- All other settings of this plugin are read-only and are only writeable if `enabled=true`.
- Plugins may additionally define their own `visible_if` rules; these take precedence.

A setting can require more than one condition:

- `visible_if=(key, value)` stays exactly as it is: one condition, referencing a setting of the same plugin.
- `visible_if_all=((key, value), ...)` adds further conditions.
- If both are set, every condition from both is required (logical AND, duplicates removed). There is no OR.
- An empty condition list means "no own condition", not "always visible": such a setting still inherits the implicit `enabled=true` rule above.

The same conditions apply in the browser and on the server. A posted plugin form is first resolved to its effective state - control values such as `enabled` or a provider selection take effect first, so enabling a plugin and filling the fields this unlocks works in one save - and only settings that are visible in that resulting state are written. A hand-crafted request can therefore neither set nor clear a hidden setting.

Settings of type `password` are write-only in the UI:

- The stored value is never sent back to the browser; the page only shows whether one is stored.
- An empty field keeps the stored secret, a non-empty field replaces it.
- Clearing is a separate, confirmed delete action for one registered password setting of one plugin, and it obeys the same conditions.
- Plugins and services keep reading the real (decrypted) value through the plugin context.


---

Examples:

```none
CrowdSec active
CrowdSec LAPI URL and credentials

Traefik log

Geoblock log
```


---
