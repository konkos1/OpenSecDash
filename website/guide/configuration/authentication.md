# Authentication

OpenSecDash is designed for an internal homelab network. The recommended setup keeps it
behind a reverse proxy, optionally with an external identity provider such as Pocket ID,
Authentik, or Authelia. Internal sign-in is optional and disabled by default, so existing
deployments keep their current reverse-proxy trust model.

## Enable internal sign-in

Open **Settings → Sign-in & users**. Enter a username and password for the first
administrator, then select **Enable internal sign-in**. OpenSecDash creates that admin,
enables sign-in, and keeps the current browser signed in.

Once enabled, visitors must sign in before opening the dashboard. Administrators can add
more users in the same Settings section.

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
internal sign-in, so you can open Settings and reset a password or repair user access.
Remove the variable and restart once more to enable internal sign-in again.

```yaml
services:
  opensecdash:
    environment:
      - OSD_AUTH_DISABLED=true # emergency switch: disables the internal sign-in
```

Keep the switch only for recovery. While it is set, every visitor who can reach
OpenSecDash has the same access as before internal sign-in was enabled.

## Security notes

HTTPS is still handled by the reverse proxy. The internal session cookie is marked
`Secure` only for HTTPS requests, and it is stored server-side so sign-out, password
resets, and deactivation can revoke it. OpenSecDash does not provide built-in 2FA,
OIDC, or password-recovery email; use an external identity provider in front of the
proxy when those controls are required.
