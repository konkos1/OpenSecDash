# Authentication

OpenSecDash is designed for an internal homelab network. The recommended setup keeps it
behind a reverse proxy, optionally with an external identity provider such as Pocket ID,
Authentik, or Authelia. Internal sign-in is optional and disabled by default, so existing
deployments keep their current reverse-proxy trust model. Once internal sign-in is
enabled, it can use local passwords, [single sign-on with OIDC](#single-sign-on-oidc), or
both.

## Enable internal sign-in

Before enabling internal sign-in:

1. Publish OpenSecDash through a reverse proxy using HTTPS on external port 443.
2. Configure a certificate that the users' browsers trust and that is valid for the
   OpenSecDash hostname.
3. Set `OSD_TRUSTED_PROXIES` explicitly to the proxy IP or the narrowest practical proxy
   network. The default private-network trust and `*` are not accepted for activation.
4. Ensure that the proxy sends `X-Forwarded-Proto: https`, `X-Forwarded-Port: 443`, and
   `X-Forwarded-Host`.

Do not trust an entire LAN or a broad private range. Every address in
`OSD_TRUSTED_PROXIES` can supply forwarded client metadata, so prefer the individual
reverse-proxy IP or a small dedicated proxy network.

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
| Viewer | View dashboard data and manage personal preferences, saved views, columns, and dashboard layout. |
| Operator | Viewer access plus explicitly approved actions and refresh/sync operations such as CrowdSec ban/unban and Proxmox sync. |
| Admin | Operator access plus Settings, users, branding, notification rules, integration configuration, debug reports, event creation, and asset inventory imports. |

Route permissions are declared in one auditable Core/plugin registry. Unknown writing
routes fail closed as Admin instead of inheriting Operator access from their HTTP method.

## Manage users

Administrators can create users, change their role, reset their password, deactivate
them, or delete them. Resetting a password and deactivating a user revokes that user's
active sessions. OpenSecDash prevents removal, deactivation, or demotion of the last
active administrator.

Every signed-in user can change their own password and personal display preferences
(language, live default, theme, accent color, and automatic page refresh) from the
account link in the header.

## Single sign-on (OIDC)

Internal sign-in can additionally use one external OpenID Connect provider, for example
Authentik, Authelia, Keycloak, or Pocket ID. Single sign-on is an extra sign-in method:
after a successful provider sign-in, OpenSecDash creates its own revocable session, and
roles, permissions, and personal settings stay exactly where they are today — local.

OpenSecDash supports exactly one provider. Everything below happens under
**Settings → Sign-in & users → Single sign-on (OIDC)**.

### Before you start

1. Internal sign-in is enabled and works with a local administrator.
2. `OSD_TRUSTED_PROXIES` names your reverse proxy explicitly, and the proxy sends
   `X-Forwarded-Proto: https`, `X-Forwarded-Port: 443`, and `X-Forwarded-Host`.
3. Users reach OpenSecDash through HTTPS on external port 443 under the configured
   hostname.
4. The OpenSecDash container trusts the TLS certificate of your provider. OpenSecDash
   has no option to skip certificate verification. Providers with a private CA need that
   CA in the container's trust store.

### 1. Register OpenSecDash with your provider

Create a confidential client (authorization code flow with PKCE) at your provider and
register exactly this redirect URL:

```text
https://dash.example.com/auth/oidc/callback
```

The redirect URL is always built from the configured authentication hostname, never from
a request header. Settings shows the exact value to copy. Request the standard scopes
`openid profile email`; no other scope, group, or role configuration is needed.

### 2. Configure and check the provider

Enter the provider's discovery URL, the client ID, and the client secret:

```text
Discovery URL:  https://id.example.com/.well-known/openid-configuration
Client ID:      opensecdash
Client secret:  <the secret from your provider>
```

**Check and save provider** fetches the discovery document before anything is stored.
The document must be reachable over HTTPS, must belong to the same host as the issuer it
declares, and must offer the authorization code flow and an asymmetric ID token
signature. Private homelab addresses are allowed; loopback, link-local, and cloud
metadata addresses are not. A failed check never replaces a working configuration.

The client secret is stored encrypted and never shown again. Leaving the field empty
keeps the stored secret; **Delete client secret** removes it and disables single sign-on.

Then use **Enable single sign-on**. **Diagnostics → Single sign-on (OIDC)** shows the
stored state of the last check without contacting the provider and without displaying
addresses, issuers, or client IDs.

### 3. Link your own admin account

Open the account page from the header, then **Single sign-on (OIDC) → Link provider
account**. This runs a complete provider sign-in and links the result to the account you
are currently signed in with.

OpenSecDash never matches accounts by email address, username, or display name. An
account is identified only by the issuer and the immutable subject from the verified ID
token, so a provider account with the same name as a local user cannot take that user
over.

### 4. Sign in through the provider once

Sign out and use **Sign in with single sign-on** on the login page. Confirm that you are
signed in with your admin role before you change anything else. Do this before switching
password sign-in off — it is what proves the whole path works.

### Create unknown users automatically

**Create unknown users automatically** is off by default. While it is off, a provider
account without a link is rejected and nothing is created.

Switched on, an unknown provider account gets a new local account with the **Viewer**
role, default preferences, and no local password. Roles are never taken from the
provider: no group, role, or permission claim is read, and an automatically created user
never becomes Operator or Admin. Promote users in the local user list when needed.

The username comes from the provider's `preferred_username` when it is valid and still
free. Otherwise OpenSecDash generates a stable, non-identifying name from the issuer and
subject. A name collision never links or takes over an existing account.

### Switch off password sign-in

Password sign-in stays on by default. **Disable password sign-in** is only accepted when
all of the following are true:

1. internal sign-in is enabled;
2. single sign-on is enabled and completely configured;
3. your own admin account is linked to the configured provider;
4. the session you are acting in was created by single sign-on itself;
5. at least one active admin stays linked to that provider.

Switching it off revokes all sessions that were created with a password, so everyone
signs in through the provider afterwards. While password sign-in is off:

- the discovery URL, client ID, client secret, and issuer are locked against changes —
  enable password sign-in again before changing providers;
- the last active admin linked to the current provider cannot be demoted, deactivated,
  deleted, or unlinked.

Disabling single sign-on always switches password sign-in back on in the same step, so
the instance can never end up with no way in.

### Change the provider or remove a link

Saving a changed discovery URL, client ID, secret, or issuer revokes all sessions that
were created with single sign-on. Existing links stay with the old issuer: after a real
provider change, every user links their account again. Admins can remove another user's
link under **Settings → Sign-in & users**, which also signs that user out everywhere.

Users can remove their own link on the account page while password sign-in is available
and their account has a local password. If a linked user has no local password, an admin
can set one with the normal password reset.

### Signing out

Signing out of OpenSecDash ends the local OpenSecDash session only. The session at your
provider stays open, so **Sign in with single sign-on** may sign you straight back in
without a password prompt. OpenSecDash does not implement provider-initiated,
back-channel, or front-channel logout. Sign out at the provider as well when you want to
end that session, and use the provider's own controls to lock an account out
immediately. OpenSecDash also stores no access or refresh tokens after sign-in and never
calls provider APIs afterwards.

### Provider outage or misconfiguration

If the provider is unreachable, its certificate is not trusted, or its configuration is
broken, single sign-on fails with a generic message and technical details stay in the
container log. As long as password sign-in is still on, nothing else changes.

If password sign-in is off, recover with the emergency switch:

1. Set `OSD_AUTH_DISABLED=true` for the container and restart it.
2. Restrict network access to the instance while the switch is active.
3. Open **Settings → Sign-in & users** and use **Enable password sign-in**, and if
   needed repair or disable the provider configuration or reset a local password.
4. Remove `OSD_AUTH_DISABLED` and restart OpenSecDash.
5. Sign in with username and password and repair single sign-on in normal operation.

The emergency switch never deletes or rewrites the stored provider configuration or the
existing links, so the repaired state is active again as soon as the variable is gone.

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

The database keeps `auth.enabled`, users, password hashes, the provider configuration,
and existing provider links while the environment override is active. Removing the
override restores the saved authentication state. If single sign-on is the only way in,
follow [Provider outage or misconfiguration](#provider-outage-or-misconfiguration).

## Security notes

HTTPS is handled by the reverse proxy. The browser, not OpenSecDash, validates the
proxy's certificate. OpenSecDash requires trusted forwarded HTTPS metadata, binds active
authentication to the configured hostname on port 443, marks the session cookie
`Secure`, and sends HTTP Strict Transport Security (HSTS). Sessions are stored
server-side so sign-out, password resets, deactivation, and hostname recovery can revoke
them. Internal sign-in can additionally use one [OpenID Connect
provider](#single-sign-on-oidc); OpenSecDash does not provide built-in 2FA or
password-recovery email. Put an external identity provider in front of the proxy when
those controls are required.

Single sign-on uses the authorization code flow with PKCE, state, and nonce. The ID
token is verified against the provider's published keys with a pinned issuer and
audience; `none` and symmetric signatures are rejected. Provider tokens are never stored
and never logged, and client secrets, codes, claims, and subjects appear in neither the
UI, the diagnostics page, nor the debug report.

Passwords use self-describing scrypt hashes. New and changed passwords use
`N=2^14,r=8,p=5`, one of OWASP's equivalent minimum scrypt profiles. A 2026-07-22
measurement in a container limited to 1 vCPU and 512 MB found about 0.123 seconds for
one hash and 1.194 seconds for five parallel hashes at roughly 98 MiB peak RSS. The
`N=2^17,r=8,p=1` alternative used about 509 MiB during the same parallel test. Existing
`N=2^14,r=8,p=1` hashes remain valid and are upgraded only after a successful sign-in.

All responses receive anti-sniffing, referrer, framing, permissions, and Content
Security Policy headers. Login, Settings, and authenticated HTML/API responses are not
cached. HSTS is sent only after the configured trusted HTTPS/443 authentication boundary
has been validated.

OpenSecDash rejects state-changing browser requests from a different origin even while
internal sign-in is disabled. This prevents a website opened in the browser from
silently changing dashboard settings or enabling sign-in. Such requests must provide a
same-origin `Origin` header or, as a legacy fallback, a same-origin `Referer` header.
Requests without verifiable origin information are rejected. Browser forms must use the
same scheme, hostname, and port as the dashboard; submitting from an alternate dashboard
hostname is intentionally rejected.
