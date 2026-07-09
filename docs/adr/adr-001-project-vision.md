# 📐 Architecture Requirements v1

> **Implementation status (2026-07-09):** Partially implemented.
> Core product, OSS licensing, homelab target, Python/FastAPI/SQLite/Jinja/HTMX/Alpine/Tailwind and Docker-oriented deployment exist. Some vision items, such as the full contribution/CLA process, remain project/process work.


# ADR-001: Project Vision


## Name

`OpenSecDash`


---

## Target group

* Homelab enthusiasts
* Self-hosters
* Small server environments
* Private VPS

Not primarily:

* Enterprise
* SOC
* SIEM
* Large companies


---

## Goal

OpenSecDash should collect, correlate, and clearly present security events, access data, and infrastructure information from various sources.

Not:

`Log viewer`

but:

`Event and insight platform`


---

## Languages / Frameworks

Backend:

```none
Python 3.13
FastAPI
Uvicorn
SQLAlchemy
SQLite
Websockets
```

Frontend:

```none
Jinja2
HTMX
Alpine.js
Tailwind CSS
```

Deployment:

```none
Docker Compose
```

Development:

```none
VS Code
GitHub
```

## Licensing

AGPL-3.0

With a clear wish for community contributions, e.g. in the form of new plugins.


## Icon

 Design:

 - modern shield as a security reference
 - dashboard/list lines for monitoring
 - network/signal arc for events, logs, and integrations
 - blue/cyan-slate color scheme matching OpenSecDash

 Integrated:

 - as favicon
 - as Apple Touch Icon

---
