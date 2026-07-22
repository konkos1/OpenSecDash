# ADR-010: Security

> **Implementation status (2026-07-09):** Implemented.
> Actions are routed through API/core/plugin services. Browser-side shell execution is not used.
> Update (2026-07-22): all HTTP responses also receive a global baseline of CSP,
> anti-framing, anti-sniffing, referrer, permissions, and appropriate cache headers.
> HSTS remains limited to the validated trusted HTTPS authentication boundary.



Actions must never be performed directly from the browser.

Always:

```none
Browser
↓
API
↓
Plugin
↓
Action
```


---

Example:

`Ban IP`

never executes shell commands in the browser.


---
