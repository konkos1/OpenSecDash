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
> Update (2026-07-22): route roles are explicit and auditable for Core and plugins;
> unknown writes fail closed as Admin. Event creation and asset inventory imports are
> Admin operations. Internal authentication remains disabled by default, with a global
> warning that unprotected visitors have full access. Password hashes use the bounded
> OWASP scrypt `N=2^14,r=8,p=5` profile and transparently upgrade the previous profile
> only after successful authentication. No OIDC, onboarding, or external-identity user
> provisioning is introduced.
> Update (2026-07-22): the production image installs Python exclusively from
> `uv.lock` in a multi-stage build with digest-pinned Python and uv bases. The Compose
> example uses a read-only root filesystem, a bounded `/tmp`, no-new-privileges,
> resource/PID limits, and only the capabilities needed to repair `/data` ownership
> before dropping to the unprivileged app user. Existing named and bind volumes retain
> this ownership-migration path.
> Update (2026-07-23): `backend/.python-version` is the canonical exact Python patch
> version for development, CI, release validation, and both Docker stages. The required
> backend check rejects drift, installs the development environment with that exact
> interpreter, and builds and smoke-tests the hardened production image for changes
> affecting the backend, plugins, Docker assets, or workflows.
> Update (2026-07-23): optional OpenID Connect sign-in exists for exactly one generic
> discovery provider. It is a sign-in method only: a verified callback creates the same
> revocable server-side OpenSecDash session, and local roles remain the only source of
> authorization. Creating unknown provider accounts as local users is a separate switch
> that is off by default and only ever creates viewers. Password sign-in stays on by
> default and can be switched off only from a proven single sign-on admin session.
> `OSD_AUTH_DISABLED=true` remains the provider-independent break-glass path. Internal
> authentication itself is unchanged: still optional and still disabled by default, with
> no onboarding and no group or role claim mapping.



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

# Single sign-on (OIDC)

Internal authentication offers one optional additional sign-in method: exactly one
generic OpenID Connect provider, configured with a discovery URL, a client ID, and a
client secret. There are no provider-specific implementations and no provider list.

```none
Sign-in methods
├── username + password   (on by default)
└── OpenID Connect        (off by default)
```

The rules are:

* A verified callback creates a normal server-side OpenSecDash session for a local user.
  Pages, APIs, WebSockets, roles, and personal preferences do not change.
* An external identity is `issuer + subject` from a verified ID token. Email,
  `preferred_username`, and display names are never sign-in keys, and existing local
  accounts are never matched or linked by them.
* Linking is started by the already signed-in user on their own account page and needs a
  complete provider sign-in plus their still valid local session.
* Creating unknown provider accounts as local users is a separate switch, off by
  default. It only ever creates active viewers with default preferences. Groups and role
  claims are not read; roles are changed only in the local admin interface.
* Local password hashes are optional, so an automatically created user has no password
  and cannot use password sign-in until an admin sets one.
* Password sign-in can be switched off, but only while authentication is on, the
  provider is enabled and complete, the acting admin is linked to the configured issuer,
  the current admin session was created by that provider, and at least one active linked
  admin remains. Switching it off revokes all password sessions.
* Authentication can never persist a state with no usable sign-in method. Disabling the
  provider switches password sign-in back on in the same transaction, and a stored "off"
  never applies while no usable provider exists.
* While password sign-in is off, the last active admin linked to the current issuer
  cannot be demoted, deactivated, deleted, or unlinked, and the provider configuration
  and issuer are locked against changes.
* `OSD_AUTH_DISABLED=true` also recovers a broken or unreachable provider: it never
  deletes or rewrites the stored OIDC configuration or identities, and it allows
  password sign-in to be switched back on, the provider to be repaired or disabled, and
  local passwords to be reset.

Deliberately out of scope: group, role, or permission mapping from claims; SCIM, LDAP,
and user synchronization; automatic deactivation of users removed at the provider;
several providers at once; storing or using access and refresh tokens after sign-in;
RP-initiated, back-channel, and front-channel logout; a switch to disable TLS
verification. Signing out ends the local OpenSecDash session only; the provider session
stays open.


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
optionally OIDC as an additional sign-in method
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

## Implementation notes (2026-07-23)

Single sign-on is implemented with Authlib's Starlette integration; OpenSecDash contains
no hand-written OIDC cryptography. Discovery, the authorization code flow with PKCE,
state, nonce, the token exchange, JWKS, and ID token verification come from the library.
Issuer and audience are pinned explicitly, only asymmetric ID token signatures are
accepted, and the clock skew allowance is 60 seconds.

Provider settings live in the existing `settings` table; the client secret uses the
encrypted `auth.oidc.client_secret` key and is never returned to HTML or the settings
API. The check that runs before saving fetches the discovery document over HTTPS only,
re-validates every redirect target against the same URL policy, and keeps connect/read
timeouts and a response size limit. Private homelab addresses stay allowed; loopback,
link-local, and cloud metadata addresses do not. Client secrets, codes, tokens, claims,
subjects, and provider responses appear in neither the UI, the logs, the diagnostics
page, nor the debug report; only stable error codes and exception class names do. Provider
connections verify TLS against the container's own trust store rather than a library
certificate bundle, so a homelab CA can be trusted through the image or
`SSL_CERT_FILE`/`SSL_CERT_DIR`; proxy environment variables stay disabled, and there is
no switch to skip certificate verification. The same transport caps every answer Authlib
reads on its own — token, JWKS, and userinfo — at the size already used for discovery, so
a broken or hostile provider cannot make the process buffer an unbounded response. Any
failure on the way to a verified ID token — including valid JSON in an unexpected shape —
ends as the same generic failed sign-in, never as an error page.

The redirect URL is always `https://<auth.hostname>/auth/oidc/callback`, built from the
validated hostname instead of a request header. The short-lived transaction state uses a
separate signed `HttpOnly`/`Secure`/`SameSite=Lax` cookie with a ten-minute lifetime and
a per-process key, so a restart during the redirect safely means "start again". Login
and link flows are marked separately, and a link callback additionally requires the
unchanged local session of the same user.

`users.password_hash` is nullable, `external_identities` stores one provider identity per
local user with a unique `issuer + subject`, and `user_sessions.auth_method` records
whether a session came from a password or from the provider. Existing sessions were
classified as `password` by the migration; existing local users and their sessions keep
working unchanged.
