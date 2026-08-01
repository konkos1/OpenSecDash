# ADR-010: Security

> **Implementation status (2026-07-09):** Implemented.
> Actions are routed through API/core/plugin services. Browser-side shell execution is not used.
> Update (2026-07-22): all HTTP responses also receive a global baseline of CSP,
> anti-framing, anti-sniffing, referrer, permissions, and appropriate cache headers.
> HSTS remains limited to the validated trusted HTTPS authentication boundary.
> Update (2026-07-24): remote integration responses are streamed through explicit
> size limits before JSON decoding. This covers GeoIP, GitHub release checks, Proxmox
> inventory, and CrowdSec login/alerts; mutation responses are not buffered.
> Local databases, key files, environment files, logs, caches, and development data
> are recursively excluded from Docker build contexts. The container executes Uvicorn
> directly as its non-root PID 1 so termination signals reach the application.
> Update (2026-08-01): remote GeoIP offers IPLocate as an HTTPS provider bound to a
> fixed EU endpoint constant, with certificate verification on, redirects not followed,
> and its required API key sent in a request header only - never in a URL, HTML, log,
> or debug report. Remote GeoIP stays disabled by default; new installations only
> pre-select the provider. `ip-api.com` remains an upgrade-compatible legacy option
> that is explicitly warned about as unencrypted HTTP, is never selected automatically,
> and is never used as a fallback for a failed lookup. Provider responses stay within
> the existing streamed size limit and are reduced to the fields OpenSecDash stores.



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
