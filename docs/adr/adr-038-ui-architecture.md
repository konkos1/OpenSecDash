# ADR-038: UI Architecture

> **Implementation status (2026-07-09):** Implemented.
> The UI is server-rendered with FastAPI/Jinja2, HTMX, Alpine.js, Tailwind, WebSocket live updates, responsive pages, plugin pages, and shared template context.



## Goal

OpenSecDash should support:

* Desktop
* Tablet
* Smartphone

equally well.


---

## Approach

Server Side Rendering (SSR)


---

Technologies:

```none
FastAPI
Jinja2
HTMX
Alpine.js
Tailwind CSS
```


---

**Not:**

```none
React
Vue
Angular
```

# Why?

OpenSecDash is primarily:

```none
Dashboard
Event Viewer
Administration
```

Not:

```none
Google Docs
Figma
Photoshop
```

Therefore, a large frontend framework adds little value.


---

# Page structure

Every page consists of:

```none
Layout
↓
Widgets
↓
Partials
```


---

Example:

`CrowdSec`

consists for example of:

```none
Active Bans Widget

Scenario Widget

Country Widget

History Widget
```


---

# HTMX

HTMX loads partial areas.


---

Example:

`CrowdSec page`

Initial:

`Server renders HTML`

After that, for example:

```none
Update top countries
↓
reload only widget
```

Not:

`rerender entire page`


---

# Live Mode

Already decided:

`Live ON (Default)`

Technically:

`WebSocket`

Flow:

```none
Event
↓
Backend
↓
WebSocket
↓
Widget
```


---

# Snapshot Mode

`Live OFF`

Then:

`no WebSocket updates`

Display, for example:

```none
As of:
20.06.2026 11:42:31
```


---

# Mobile First

**Mandatory.**


---

Breakpoints:

```none
Phone
Tablet
Desktop
```


---

# Tables

On smartphones, never:

`15 columns`


---

but:

`card view`

or:

`horizontal scrolling`

depending on data type.


---

## CrowdSec example

Desktop:

`IP | Country | Scenario | Duration`


---

Smartphone:

```none
IP

Country
Scenario
Duration
```

→ as a card.


---

# Navigation

Desktop:

`Sidebar`


---

Mobile:

`Hamburger menu`


---

Same structure.


---

# Global Search

Always visible.


---

Search:

```none
IP
Hostname
Asset
```


---

leads directly to:

```none
IP Explorer

Asset Explorer
```


---

# Domain display

Decided:

`OpenSecDash`

remains visible.


---

Header, for example:

```none
OpenSecDash

mydomain.de
```

on every page.


---

# Branding

Settings:

```none
Domain

Logo

Favicon
```


---

but:

`OpenSecDash`

never hide completely.


---

# Theme

V1:

```none
Dark
Light
Auto
```

plan directly.


---

# Result

This defines the complete UI architecture:

```none
FastAPI
+
Jinja2
+
HTMX
+
Alpine.js
+
Tailwind
+
WebSocket Live Updates
+
SSR
```


---
