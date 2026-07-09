# ADR-040: Event Severity Model

> **Implementation status (2026-07-09):** Implemented.
> Severity values and access/status classification exist in the event service; CrowdSec/GeoBlock protection events are warnings and scanner insights can be higher severity.



Definition:

```none
info
warning
error
critical
```


---

Examples:

### Access

```none
200
↓
info
```


---

### 404

`warning`


---

### CrowdSec Ban

`warning`

Reason: A ban is a successful protection/defense decision, not a technical error of the application. Escalations to `error`/`critical` are created by separate correlations/insights, e.g. scanner or attack patterns.


---

### GeoBlock Block

`warning`

Reason: A GeoBlock is also a successful protection decision.

---

### Scanner detected

`critical`


---

Later, for example:

`show only Critical`

is immediately possible.


---
