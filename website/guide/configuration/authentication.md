# Authentication

OpenSecDash is designed for an internal homelab network. The recommended setup keeps it
behind a reverse proxy, optionally with an external identity provider such as Pocket ID,
Authentik, or Authelia. Internal sign-in is optional and disabled by default, so existing
deployments keep their current reverse-proxy trust model.

## Enable internal sign-in

Before enabling internal sign-in:

1. Publish OpenSecDash through a reverse proxy using HTTPS on external port 443.
2. Configure a certificate that the users' browsers trust and that is valid for the
   OpenSecDash hostname.
3. Set `OSD_TRUSTED_PROXIES` explicitly to the proxy IP or the narrowest practical proxy
   network. The default private-network trust and `*` are not accepted for activation.
4. Ensure that the proxy sends `X-Forwarded-Proto: https`, `X-Forwarded-Port: 443`, and
   `X-Forwarded-Host`.

Open **Diagnostics → Authentication transport** through the intended hostname to verify
the current proxy peer and forwarded HTTPS, port, and hostname checks. The page reports
only statuses and remediation guidance; it does not display configured proxy IPs or
network ranges.

Open **Settings → Sign-in & users** through that HTTPS hostname. Enter the hostname
without `https://`, a port, path, or trailing dot, then enter the username and password
for the first administrator. OpenSecDash verifies the trusted proxy, HTTPS port 443, and
hostname before it creates the admin and enables sign-in.

Once enabled, visitors must sign in before opening the dashboard. Administrators can add
more users in the same Settings section. All user-facing pages, static files, APIs, and
the event WebSocket remain bound to the configured HTTPS hostname. `/health` and
`/ready` stay available for direct internal container and proxy health checks.

## Roles

| Role | Access |
| --- | --- |
| Viewer | View dashboard data and manage saved views and dashboard layout. |
| Operator | Viewer access plus actions such as CrowdSec ban and unban. |
| Admin | Operator access plus Settings and user management. |

## Manage users

Administrators can create users, change their role, reset their password, deactivate
them, or delete them. Resetting a password and deactivating a user revokes that user's
active sessions. OpenSecDash prevents removal, deactivation, or demotion of the last
active administrator.

Every signed-in user can change their own password and personal display preferences
(language, live default, theme, accent color, and automatic page refresh) from the
account link in the header.

## Locked out of the web UI

Set `OSD_AUTH_DISABLED=true` for the container and restart it. This temporarily disables
internal sign-in and the HTTPS-hostname binding, so you can open Settings directly and
reset a password or repair user access.

```yaml
services:
  opensecdash:
    environment:
      - OSD_AUTH_DISABLED=true # emergency switch: disables the internal sign-in
```

Keep the switch only for recovery. While it is set, every visitor who can reach
OpenSecDash has the same access as before internal sign-in was enabled.

To repair a changed hostname:

1. Set `OSD_AUTH_DISABLED=true` and restart OpenSecDash.
2. Restrict network access to the instance while the emergency switch is active.
3. Open **Settings → Sign-in & users** and save the repaired OpenSecDash hostname.
   Saving it revokes all existing sessions but does not activate sign-in.
4. Configure the trusted reverse proxy, HTTPS port 443, and its certificate for that
   hostname.
5. Remove `OSD_AUTH_DISABLED` and restart OpenSecDash.

The database keeps `auth.enabled`, users, and password hashes while the environment
override is active. Removing the override restores the saved authentication state.

## Security notes

HTTPS is handled by the reverse proxy. The browser, not OpenSecDash, validates the
proxy's certificate. OpenSecDash requires trusted forwarded HTTPS metadata, binds active
authentication to the configured hostname on port 443, marks the session cookie
`Secure`, and sends HTTP Strict Transport Security (HSTS). Sessions are stored
server-side so sign-out, password resets, deactivation, and hostname recovery can revoke
them. OpenSecDash does not provide built-in 2FA, OIDC, or password-recovery email; use an
external identity provider in front of the proxy when those controls are required.

OpenSecDash rejects state-changing browser requests from a different origin even while
internal sign-in is disabled. This prevents a website opened in the browser from
silently changing dashboard settings or enabling sign-in. Such requests must provide a
same-origin `Origin` header or, as a legacy fallback, a same-origin `Referer` header.
Requests without verifiable origin information are rejected. Browser forms must use the
same scheme, hostname, and port as the dashboard; submitting from an alternate dashboard
hostname is intentionally rejected.
