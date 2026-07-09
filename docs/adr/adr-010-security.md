# ADR-010: Security

> **Implementation status (2026-07-09):** Implemented.
> Actions are routed through API/core/plugin services. Browser-side shell execution is not used.



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
