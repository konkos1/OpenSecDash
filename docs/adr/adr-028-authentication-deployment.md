# ADR-028: Authentication & Deployment

> **Implementation status (2026-07-22):** Partially implemented.
> Docker-oriented single-container deployment, SQLite, reverse-proxy trust model,
> proxy-header middleware (X-Forwarded-For/-Proto/-Host/-Port from trusted proxies,
> configured via OSD_TRUSTED_PROXIES), API-side actions, health/ready endpoints,
> update checks, and optional internal user management (admin/operator/viewer roles,
> disabled by default) exist. Internal authentication is bound to an explicitly trusted
> reverse proxy, HTTPS port 443, and one configured hostname. `/health` is a process-only
> liveness check; `/ready` performs one read-only database ping after startup and never
> triggers migrations, seeding, secret rotation, or event maintenance.



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
X-Forwarded-Port
```


---

# HTTPS

**Not** by OpenSecDash.

HTTPS is handled by the reverse proxy.

The reverse proxy and browser own certificate issuance and validation. OpenSecDash
cannot inspect the proxy's server certificate after TLS termination.


---

# Internal authentication

Internal authentication is optional and disabled by default. It provides:

```none
Admin
Operator
Viewer
```

Activation requires all of the following:

* `OSD_TRUSTED_PROXIES` is explicitly configured with a proxy IP or CIDR; implicit
  defaults and wildcard trust do not establish the authentication boundary.
* The direct peer matches that explicit trust configuration.
* The proxy supplies `X-Forwarded-Proto: https`.
* The proxy supplies `X-Forwarded-Port: 443`.
* The administrator enters a valid DNS hostname that exactly matches the trusted
  `X-Forwarded-Host`.

The normalized hostname is persisted as `auth.hostname`. While authentication is
active, user-facing HTTP and WebSocket traffic must continue to use that trusted
HTTPS/443 origin. `/health` and `/ready` remain exempt so internal health checks do not
depend on the public proxy path. Valid authenticated HTTP responses send HSTS.

`OSD_AUTH_DISABLED=true` is the break-glass override. It disables authentication and
the hostname boundary without deleting users, password hashes, or the persisted enabled
state. While the override is active, Settings allows the hostname to be repaired and
revokes existing sessions. The override must be removed and OpenSecDash restarted to
restore authentication.


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
optional internal user management
+
optionally e.g. Pocket-ID/Authentik/Authelia in front
```


---

---

## Implementation notes (2026-07-09)

The current implementation follows the V1 trust model without internal user management. It provides API-side action execution and health/ready-style operational endpoints.

Dedicated proxy-header middleware is present in `app/web/proxy_headers.py`. It accepts
`X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`, and `X-Forwarded-Port` only from trusted
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

## Implementation notes (2026-07-17)

Internal sign-in activation and authenticated traffic now use the stricter
HTTPS/443/hostname boundary specified above. The proxy middleware records whether
forwarded metadata came from an explicitly configured peer so implicit private-network
defaults and wildcard trust cannot activate internal authentication. The break-glass
override also supports hostname repair and session revocation.

Login throttling uses independent account and direct-peer buckets. The account bucket
limits attempts against one normalized username regardless of forwarded client IP, while
the higher-threshold peer bucket limits password spraying across usernames. The proxy
middleware preserves the direct peer address before applying `X-Forwarded-For`, so an
overly broad trusted-proxy configuration cannot bypass both buckets by rotating
forwarded addresses. Deployments must still restrict `OSD_TRUSTED_PROXIES` to individual
proxy addresses or the narrowest practical dedicated proxy network.
