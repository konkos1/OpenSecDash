# ADR-008: Data Refresh

> **Implementation status (2026-07-09):** Implemented.
> The app has a WebSocket endpoint for live event change notifications and snapshot-style page/table behavior.



## Live mode (default)

Optionally switchable:

```none
⚪ Live OFF

🟢 Live ON
```

When Live ON:

```none
WebSocket
↓
new events appear
```

Chronological.

Newest events arrive.


---

## Snapshot mode

Flow:

```none
Load data (or, since live mode was active before, it is already loaded)
↓
Freeze view
```

After that:

`nothing`

Filterable.

Sortable.

Static.

…until the user actively refreshes.

Button:

`↻ Refresh`

When live is disabled:

```none
As of: YYYY-MM-DD HH:mm:ss

for example:
As of: 2026-06-19 11:42:31
```

display.


---
