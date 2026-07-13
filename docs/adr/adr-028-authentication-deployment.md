# ADR-028: Authentication & Deployment

> **Implementation status (2026-07-13):** Partially implemented.
> Docker-oriented single-container deployment, SQLite, reverse-proxy trust model,
> proxy-header middleware (X-Forwarded-For/-Proto/-Host from trusted proxies,
> configured via OSD_TRUSTED_PROXIES), API-side actions, health/ready endpoints,
> update checks, and optional internal user management (admin/operator/viewer roles,
> disabled by default) exist.



## Goal

OpenSecDash should be as easy as possible to deploy for homelabs.


---

# Deployment model

## Primary

Docker Compose


---

Official installation, for example:

```yaml
services:
  opensecdash:
    image: ghcr.io/opensecdash/opensecdash:latest
```

Why?

Homelab users today typically run:

* Docker
* Docker Compose
* Unraid
* Proxmox LXC + Docker
* Synology Docker


---

# Secondary

Bare Metal


---

Later, for example:

`pip install opensecdash`

possible.

**But not the focus of V1.**


---

# Container structure

Initially deliberately use only one container.

```none
OpenSecDash
├── FastAPI
├── SQLite
├── Plugins
└── Webserver
```

Not:

```none
Frontend
Backend
Database
Redis
Worker
```

That would be unnecessarily complex for the target group.


---

# Data directory

Persistent data:

`/app/data`


---

Contains for example:

```none
database.sqlite
uploads/
plugins/
backups/
```


---

Docker volume, for example:

```yaml
volumes:
  - ./data:/app/data
```


---

# Reverse Proxy

OpenSecDash runs by default behind, for example:

* Traefik
* Nginx
* Caddy
* etc.


---

Therefore:

## Support proxy headers

```none
X-Forwarded-For
X-Forwarded-Proto
X-Forwarded-Host
```


---

# HTTPS

**Not** by OpenSecDash.

HTTPS is handled by the reverse proxy.


---

# Authentication V1

## V1

OpenSecDash trusts the upstream reverse proxy.


---

# Internal auth later

e.g. V2/V3.

Possible roles:

```none
Admin
Operator
Viewer
```

**But not in V1.**


---

# API security

All actions run server-side.

Example:

`Ban IP`


---

Browser:

```none
POST /api/actions/ban
↓
Backend
↓
CrowdSec Plugin
↓
CrowdSec LAPI
```


---

Never:

```none
Browser
↓
Shell
```


---

# Action Confirmation

For dangerous actions:

```none
Ban
Unban
Delete
```

always confirmation.


---

Example:

```none
Really ban IP 1.2.3.4 for 4 hours?

[Cancel]
[Ban]
```


---

# API Tokens

Not required for V1.

But **prepare** data model.

Later, for example:

```none
Read Only

Read + Actions

Admin
```


---

# Backup

Setting:

`Backup database`


---

Export, for example:

```none
SQLite
ZIP
JSON
```


---

# Restore

Select file, for example:

```none
backup.zip
↓
Import
↓
Restart
```


---

# Update strategy

Docker Pull.


---

UI shows, for example:

```none
OpenSecDash Version:
1.2.0

Latest Version:
1.3.0

Update available
```


---

# Health Endpoint

For monitoring:

`/health`


---

Response:

```json
{
  "status": "ok"
}
```


---

# Ready Endpoint

For container orchestration:

`/ready`


---

Checks:

* Database reachable
* Plugin system loaded


---

# Result

V1 deployment:

```json
Docker Compose
+
SQLite
+
Reverse Proxy
+
no internal user management
+
optionally e.g. Pocket-ID/Authentik/Authelia in front
```


---

---

## Implementation notes (2026-07-09)

The current implementation follows the V1 trust model without internal user management. It provides API-side action execution and health/ready-style operational endpoints.

Dedicated proxy-header middleware is present in `app/web/proxy_headers.py`. It accepts
`X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host` only from trusted
peer IPs. Loopback and private networks are trusted by default; the set is
configurable or disableable with `OSD_TRUSTED_PROXIES`. Headers from untrusted
sources are discarded. Reverse-proxy deployment remains the intended deployment
model.

## Implementation notes (2026-07-13)

Optional internal sign-in is enabled with the `auth.enabled` setting and remains off by
default; `OSD_AUTH_DISABLED` is the break-glass switch. It provides the `admin`,
`operator`, and `viewer` roles described above, with server-side DB sessions whose
tokens can be revoked. The `api_tokens` table prepares read, actions, and admin scopes;
it has no endpoints or UI. The reverse-proxy trust model remains the documented default.
