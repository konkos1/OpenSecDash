# ADR-025: Mobile First UI

> **Implementation status (2026-07-09):** Partially implemented.
> Templates use responsive Tailwind/HTMX/Alpine patterns and mobile-friendly layouts. Full PWA/offline behavior is not implemented.



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
