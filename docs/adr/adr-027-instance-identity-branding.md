# ADR-027: Instance Identity & Branding

> **Implementation status (2026-07-13):** Implemented.
> Domain identity, additive custom logo, custom favicon including PWA support, instance description, and selectable accent color are implemented; `theme_color` remains an extension for later.



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

Scalar values use the existing `settings` table instead of a new `instance_settings`
table: `domain`, `instance_description`, and `instance_accent_color`. This is a
documented deviation from the original table proposal: the domain was already
stored as a setting key, and keeping the scalar values together preserves that
existing convention.

Logo and favicon files are stored in `instance_files`:

```none
kind
filename
content_type
data
updated_at
```

The image data is kept as a BLOB in the database, which is persisted in the
`/data` volume and included in normal database backups without an additional
mount. It is delivered through `/instance/logo` and `/instance/favicon`.

Uploads are validated by magic bytes. Favicons accept PNG, SVG, WEBP, or ICO up
to 512 KB. Logos accept PNG, SVG, WEBP, or JPEG up to 1 MB.

`instance_accent_color` accepts `blue`, `green`, `orange`, or `red` and defaults
to `blue`. CSS variables and a `data-accent` attribute on the `<body>` apply it
to accent UI elements; semantic status and error colors remain separate.


---

# Extension for later

Accent color is implemented through `instance_accent_color`. The reserved
`instance_theme_color` setting key leaves room for `theme_color`, but it is
deliberately not implemented yet. Browser and PWA chrome colors remain unchanged.


---

---

## Implementation notes (2026-07-13)

Custom branding always supplements the OpenSecDash name and icon; it never replaces
them.
