# ADR-024: Dashboard Architecture

> **Implementation status (2026-07-09):** Partially implemented.
> Dashboard cards, rollups, deltas, feeds, and responsive templates exist. User-managed widget layout and fully plugin-owned dashboard widgets remain planned.



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

Later:

`Geoblock Heatmap`


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

## Implementation notes (2026-07-09)

The dashboard is currently implemented as core-rendered server-side pages and templates with metric cards, recent security context, top countries/hours, rollup comparisons, and responsive layout.

User-managed widget layout and fully plugin-owned dashboard widgets remain planned. The `widget` capability exists declaratively.

