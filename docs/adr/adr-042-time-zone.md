# ADR-042: Time Zone

> **Implementation status (2026-07-09):** Implemented.
> Timezone settings, auto/browser handling, UTC normalization, and datetime template formatting exist.



Default:

`Automatically detect browser time zone`

Optional in the settings:

```none
Europe/Berlin
UTC
America/New_York
...
```


All timestamps shown in the UI are rendered through the central `datetime` template filter.

Rules:

- Stored naive date values are interpreted as UTC.
- With `timezone=auto`, the browser formats the time in its local time zone.
- With an explicit IANA time zone, e.g. `Europe/Berlin`, this time zone is used for display.
- Dashboard time ranges such as “today” are based on the configured UI time zone.

---
