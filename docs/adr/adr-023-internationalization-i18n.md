# ADR-023: Internationalization (i18n)

> **Implementation status (2026-07-09):** Implemented.
> EN/DE locales exist in core and plugins, with setting-based language selection and fallback behavior.



## Goal

OpenSecDash should be multilingual.

V1:

`EN`
`DE`


---

## Default language

`EN`

Why?

* OSS project
* GitHub
* international community
* plugin authors


---

## User language

In the settings:

```none
Language

English
Deutsch
```


---

## Storage

In the database:

`settings.language`

Example:

`en`

or:

`de`


---

## What is translated?

### UI

Examples:

```none
Dashboard
Settings
CrowdSec
Assets
```


---

### Buttons

Examples:

```none
Refresh
Live
Save
Cancel
```


---

### Status messages

Examples:

```none
Source Healthy
Plugin Error
Update Available
```


---

### Dashboard Widgets

Examples:

```none
Top Countries
Active Bans
```


---

## What is not translated?

Log data.

Example:

`crowdsecurity/http-probing`

remains:

`crowdsecurity/http-probing`


---

Also:

`/wp-login.php`

or:

`GET`
`POST`

remain unchanged.


---

## Plugin support

Plugins may provide their own language files.

Example:

```none
plugins/
└── crowdsec/
    ├── plugin.py
    ├── locales/
    │   ├── en.json
    │   └── de.json
```

This lets third-party plugins localize their own texts.


---

## Fallback

If a translation is missing:

```none
DE
↓
EN
↓
Show key
```

Example:

`widget.active_bans`


---

## Future (e.g. V2)

Additional languages can be added later:

```none
FR
ES
IT
NL
```

without architectural changes.


---
