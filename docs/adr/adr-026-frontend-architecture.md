# ADR-026: Frontend Architecture

> **Implementation status (2026-07-13):** Implemented.
> The app uses FastAPI, Jinja2, HTMX, Alpine.js, Tailwind CSS, WebSocket live
> notifications, and server-side rendering. PWA is implemented (installable, app icon,
> standalone mode, offline fallback page); offline operation and push notifications
> remain intentionally out of scope, as decided in this ADR.
> Deviation: charts are rendered as server-side lists, bars, and an SVG world map
> instead of Chart.js — in line with the "use as little JavaScript as possible"
> principle.



## Goal

The frontend should:

* be fast
* have little complexity
* render server-side
* support live updates
* work on mobile
* be easy to understand for OSS contributions


---

# Do not use

Deliberately avoid:

```none
React
Next.js
Vue
Nuxt
Angular
SvelteKit
```

Not because they are bad.

But because then you practically have to maintain two projects:

```none
Frontend
+
Backend
```


---

# Architecture

```none
FastAPI
+
Jinja2
+
HTMX
+
Alpine.js
+
Tailwind CSS
```


---

# Why HTMX?

```none
Server delivers HTML fragment
↓
Table is replaced
```

**No**:

```none
JSON API
↓
Frontend State
↓
React Components
↓
Re-Rendering
```

This massively reduces:

* Complexity
* Build process
* JavaScript


---

# Alpine.js

Only for small interactions.

Examples:

```none
Open menu
Dark Mode
Filter dropdown
Modal dialog
```

**Not**:

`complete business logic`


---

# Tailwind CSS

Why?

The app needs:

```none
Desktop
Tablet
Phone
```


---

With Tailwind:

`<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4">`

Done.


---

# Component model

Not:

`React Components`


---

But:

`Jinja Components`


---

Example:

```none
templates/components/

card.html
table.html
widget.html
modal.html
badge.html
```


---

# Page structure

## Base Layout

`base.html`

contains:

```none
Navigation
Theme
Language
Footer
```

All pages inherit from it.


---

# Dashboard

`dashboard.html`


---

Widgets:

`widgets/`

Example:

```none
widgets/active_bans.html
widgets/top_countries.html
widgets/event_feed.html
```


---

# Plugin pages

Plugin:

`CrowdSec`

registers:

`/crowdsec`

Template:

`plugins/crowdsec/templates/`

The core does not know the page.


---

# Live mode

Technically:

`WebSocket`


---

Not:

`AJAX every 2 seconds`


---

Example:

```none
Browser
↓
WebSocket
↓
new event
↓
table extended
```


---

# Snapshot mode

When switching:

`Live OFF`

WebSocket is closed.

View remains frozen.


---

# Charts

Use as little JavaScript as possible.


---

For V1:

`Chart.js`

is completely sufficient.


---

Use cases:

```none
Top countries
Trend lines
Status codes
Scenarios
```


---

# Dark Mode

Setting:

```none
System
Light
Dark
```


---

# PWA

Prepare from the beginning.


---

Features:

```none
Installable
App Icon
Standalone mode
```


---

Not necessary for V1:

```none
Offline operation
Push Notifications
```


---

# Build system

Deliberately keep it minimal:

```none
uv
FastAPI
Tailwind
```


---

No:

```none
Webpack
Vite
Node build chains
```

if avoidable.


---

# Result

```none
Backend:
FastAPI

Templates:
Jinja2

Interactivity:
HTMX

UI State:
Alpine.js

Styling:
Tailwind

Live:
WebSocket

Charts:
Chart.js

Mobile:
Responsive + PWA
```


---
