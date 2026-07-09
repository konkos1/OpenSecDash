# ADR-027: Instance Identity & Branding

> **Implementation status (2026-07-09):** Partially implemented.
> Domain/favicon-related settings and OpenSecDash branding are represented in UI/settings. Full custom logo/accent-color behavior remains planned.



## Principle

OpenSecDash always remains visible.

The project name cannot be hidden.


---

## Header

Desktop, for example:

```none
🛡 OpenSecDash

sec-dash.example.com
```


---

or:

```none
🛡 OpenSecDash

mydomain.de
```


---

Mobile, for example:

```none
🛡 OpenSecDash
mydomain.de
```


---

The domain is therefore the instance identifier.

Not the product name.


---

## Settings

### Domain Name

Field:

`Primary Domain`

Example:

`mydomain.de`


---

Display:

### Dashboard

```none
OpenSecDash

mydomain.de
```


---

### All subpages

```none
OpenSecDash
mydomain.de
```


---

### Browser title

Example:

`OpenSecDash · mydomain.de`


---

### PWA

Example:

```none
OpenSecDash
mydomain.de
```


---

## Custom logo

Do not allow:

`Replace logo`

but:

`Add logo`


---

Example header:

```none
[Custom Logo]

🛡 OpenSecDash

mydomain.de
```


---

or:

```none
🛡 OpenSecDash
mydomain.de

[Homelab Logo]
```


---

This keeps the following always visible:

`OpenSecDash`

but the instance gets its own identity.


---

## Custom favicon

Allow:

```none
PNG
SVG
WEBP
ICO
```

Usage:

* Browser tab
* Bookmark
* PWA Icon


---

# Technical implementation

New table:

`instance_settings`


---

Fields:

```none
instance_domain
instance_logo
instance_favicon
instance_description
```


---

# Extension for later

Leave room directly for the following fields:

```none
theme_color
accent_color
```


---

Example:

```none
Blue
Green
Orange
Red
```

This could later color the entire UI.


---

---

## Implementation notes (2026-07-09)

The current implementation keeps OpenSecDash visible and includes settings/UI support related to the instance domain and favicon. Full custom logo replacement/addition behavior and theme/accent color customization remain planned.

