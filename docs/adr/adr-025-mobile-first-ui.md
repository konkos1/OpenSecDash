# ADR-025: Mobile First UI

> **Implementation status (2026-07-13):** Implemented.
> Responsive templates (burger navigation, card views for tables, stacked dashboard
> widgets, touch-friendly actions, visible live/snapshot indicator) and installable PWA
> (manifest, icons, service worker with offline fallback page). As decided in this ADR,
> there is no offline operation: the service worker never caches pages or data, it only
> serves a static fallback page when the network is unavailable.
> Deviation: pages render fully server-side and synchronously; progressive/deferred
> widget loading with per-widget loading states (so a page appears immediately while its
> widgets fetch their data) is not part of this work. On very large databases a page can
> therefore block on the server render before it becomes usable.
> Update (2026-07-13): the synchronous-render limitation above is addressed by the
> progressive-widget-loading effort.
> The heavy pages (`/`, `/events`, `/rollups`, `/ip/{ip}`, `/assets/system/{id}`, and the
> plugin pages `/access` and `/crowdsec`) now render an immediate shell and load their
> expensive widgets/sections progressively via HTMX (`hx-trigger="load"`), each showing an
> `aria-busy` skeleton while its data loads; the live WebSocket refresh is unaffected.
> Scope: loading is at section granularity (a page's heavy block loads as one deferred
> fetch, not each widget independently); a per-widget-independent variant and an explicit
> fetch-error/retry state are not implemented. Widget query performance itself is tracked
> separately.
> Deviation: installability and the offline fallback were verified on desktop Chrome and
> on `localhost`; they were not verified on a real iOS or Android device.



## Goal

OpenSecDash must be fully usable on smartphones, tablets, and desktop devices.

Not:

```none
Desktop UI
+
mobile workaround
```

but:

`Responsive UI`


---

# Supported devices

## Smartphone

Example:

```none
iPhone
Android
```

Width:

`320px+`


---

## Tablet

Example:

```none
iPad
Android Tablet
```


---

## Desktop

Example:

`Browser`


---

# Principle

Every function must be usable on the phone.

Not:

```none
Desktop:
everything

Mobile:
read-only
```


---

But:

```none
Desktop:
everything

Mobile:
everything
```


---

# Navigation

Desktop, for example:

```none
┌───────────────┐
│ Dashboard     │
│ CrowdSec      │
│ Access        │
│ Assets        │
│ Events        │
│ Settings      │
└───────────────┘
```


---

Mobile:

`☰`

Hamburger menu.


---

# Dashboard

Desktop, for example:

```none
┌──────┬──────┬──────┐
│ Ban  │ Geo  │ Apps │
└──────┴──────┴──────┘
```


---

Mobile, for example:

```none
┌──────┐
│ Ban  │
├──────┤
│ Geo  │
├──────┤
│ Apps │
└──────┘
```

→ Widgets stack vertically.


---

# Tables

On mobile devices, this must never happen:

`← horizontal scrolling →`

**for normal usage.**


---

Instead:

## Desktop

```none
IP
Country
Event
Time
```


---

## Mobile

```none
IP: 1.2.3.4
Country: RU
Event: Geoblock
Time: 14:22
```

→ as card view.


---

# Event Feed

Desktop:

`Table`


---

Mobile:

`Cards`


---

Example:

```none
1.2.3.4
RU
CrowdSec Ban
2 minutes ago
```


---

# IP Explorer

Perfect for mobile.

Example:

```none
IP

1.2.3.4

Country
RU

ASN
AS12345

CrowdSec Bans
3

Geoblocks
42
```


---

# Actions

Buttons must be finger-friendly.

Not:

`[Ban]`

with 16px.


---

But:

`large touch area`


---

# Dashboard Widgets

Widget sizes should be responsive.

Desktop:

`3 columns`


---

Tablet:

`2 columns`


---

Smartphone:

`1 column`


---

# Live mode

Especially important on mobile devices.

When active:

```none
🟢 LIVE
```

always visible.


---

When disabled:

```none
⚪ SNAPSHOT

As of:
2026-06-19 21:42:31
```

always visible.


---

# Charts

Basically:

`no complex BI charts`


---

Prefer:

```none
Lines
Bars
Heatmaps
```


---

They must remain readable on 6-inch displays.


---

# PWA support

OpenSecDash should be installable:

```none
iPhone
Android
Desktop Browser
```


---

This creates:

`OpenSecDash`

as a quasi-native app.


---

# Offline mode

Not necessary for V1.


---
