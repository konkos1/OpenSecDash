# ADR-024: Dashboard Architecture

> **Implementation status: Implemented (2026-07-11).**
> The dashboard is a descriptor-backed widget container with a core registry and
> plugin-owned widgets supplied through `Plugin.dashboard_widgets()`.
> Counter, table, feed, trend, and the core-only map widget are rendered by core templates,
> while user-managed visibility and ordering are stored in SQLite under
> user-specific `ui.dashboard_layout.user.<id>` settings.
> The heatmap is a separately managed core widget; layout V1 is a linear responsive list with
> up/down movement, not a freely positioned WYSIWYG or drag-and-drop grid.



## Goal

The dashboard should not be hard-coded.

It should be a widget container.


---

Not:

```none
Dashboard
├── CrowdSec
├── Geoblock
├── Assets
```

hard-coded.


---

But:

```none
Dashboard
↓
loads widgets
```


---

Dashboard widgets must be responsive.

Desktop:
3-4 columns

Tablet:
2 columns

Mobile:
1 column


---

# Widget types

## Counter Widget

Example:

```none
Active bans

12
```


---

```none
Geoblocks today

182
```


---

## Trend Widget

Example:

`CrowdSec last 30 days`


---

`404 last 30 days`


---

## Table Widget

Example:

`Top countries`


---

`Top scenarios`


---

## Feed Widget

Example:

`Latest events`


---

## Map Widget

The core-owned top-countries heatmap is independently shown, hidden, and moved with the
other dashboard widgets. Plugins cannot register map widgets.


---

# Dashboard Layout

Plan directly grid-based.

Example:

```none
┌─────────┬─────────┬─────────┐
│ Bans    │ Blocks  │ Assets  │
├─────────┼─────────┼─────────┤
│ Top countries      │ Feed   │
├────────────────────┼────────┤
│ Trend              │ Feed   │
└────────────────────┴────────┘
```


---

# Widget registration

Plugins may register widgets.

Example:

```python
register_widget(
    id="top_countries",
    type="table"
)
```


---

# Dashboard configuration

User can:

```none
show
hide
move
```

widgets.


---

Stored in SQLite.


---

# Default Dashboard V1

Start very lean:

### Security

```none
Active bans
Geoblocks today
```


---

### Activity

`Access Events today`


---

### Assets

```none
Installed apps
Update available
```


---

### Trends

```none
Top countries
Top scenarios
```


---

### Feed

`Latest security events`


---

---

## Implementation notes (2026-07-11)

The dashboard is implemented as core-rendered server-side pages and templates with
metric cards, recent security context, top countries/hours, rollup comparisons, and
responsive layout. Plugins contribute validated descriptors rather than HTML. The
heatmap remains a fixed core block (Map is "later"), and no new metrics were added.
Dashboard layout is stored per internal user when internal authentication is enabled;
the legacy global layout remains the first-use default and is retained for deployments
without internal authentication.
