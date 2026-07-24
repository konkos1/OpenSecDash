# Authentication

OpenSecDash is designed for an internal homelab network. The recommended setup keeps it
behind a reverse proxy, optionally with an external identity provider such as Pocket ID,
Authentik, or Authelia. A new installation starts with internal sign-in enabled and asks
the first visitor to create the first administrator. Existing installations keep whatever
they had when they were updated. Internal sign-in uses local passwords,
[single sign-on with OIDC](#single-sign-on-oidc), or both.

Which of the three situations applies to you:

| Situation | What happens |
| --- | --- |
| New installation | The first visit shows the [first-time setup](#first-time-setup-new-installations). Nothing else is reachable until it is finished. |
| Update of an installation with internal sign-in already enabled | Nothing changes. No extra step, no banner. |
| Update of an installation that was still open | Everything keeps working, and a permanent [security prompt](#updated-installations-that-are-still-open) asks you to decide. |

## Prepare the reverse proxy first

Setting internal sign-in up needs a correctly configured reverse proxy. Do this before
the first start of a new installation:

1. Publish OpenSecDash through a reverse proxy using HTTPS on external port 443.
2. Configure a certificate that the users' browsers trust and that is valid for the
   OpenSecDash hostname.
3. Set `OSD_TRUSTED_PROXIES` explicitly to the proxy IP or the narrowest practical proxy
   network. The default private-network trust and `*` are not accepted here.
4. Ensure that the proxy sends `X-Forwarded-Proto: https`, `X-Forwarded-Port: 443`, and
   `X-Forwarded-Host`.

Do not trust an entire LAN or a broad private range. Every address in
`OSD_TRUSTED_PROXIES` can supply forwarded client metadata, so prefer the individual
reverse-proxy IP or a small dedicated proxy network.

The setup page itself lists these requirements with a live status for the request you
are looking at it with. On an already running instance, **Diagnostics → Authentication
transport** shows the same checks. Both report only statuses and remediation guidance;
neither displays configured proxy IPs or network ranges.

## First-time setup (new installations)

A brand-new installation starts in a narrow setup mode. Every page request is redirected
to `/onboarding`, API requests answer `503`, and the event WebSocket is closed before any
data is read. `/health` and `/ready` stay available for container and proxy health
checks. Dashboard, settings, plugins, and the login page are not reachable yet.

The setup page shows no instance data, navigation, or account information. Anyone who can
reach the instance may look at it and fill the form in — that alone gives no access to
anything. Only a submission that arrives through the trusted HTTPS/443 proxy boundary
with the matching hostname is accepted.

1. Pick your language at the top of the page. The page then appears completely and only
   in that language. Switching just reloads the page; nothing is stored yet.
2. Enter the **authentication hostname**: the HTTPS hostname your browser reaches
   OpenSecDash with, for example `dash.example.com`. Enter it without `https://`, a port,
   a path, or a trailing dot. It has to match `X-Forwarded-Host` from your proxy exactly.
3. Enter username, password, and password confirmation for the first administrator. The
   password needs at least 8 characters. Keep it safe — without a second admin there is
   no password reset except the emergency switch below.
4. Submit. Hostname, the first administrator, the chosen language, the enabled sign-in,
   and the finished setup are stored together in one transaction. If anything fails,
   nothing is stored and the setup stays open.

Finishing the setup does **not** sign you in. OpenSecDash redirects to `/login` in the
language you chose, and you sign in there with the account you just created. That first
sign-in is what proves the whole path works.

If two people open the setup at the same time, only one submission wins. The other gets a
harmless "already finished" answer, and no second administrator is created.

Afterwards, visitors must sign in before opening the dashboard. Administrators add more
users under **Settings → Sign-in & users**. All user-facing pages, static files, APIs, and
the event WebSocket are bound to the configured HTTPS hostname. Single sign-on is
configured after this first local sign-in; see
[Single sign-on (OIDC)](#single-sign-on-oidc).

## Updated installations that are still open

An installation that was running without internal sign-in stays exactly as it was after
the update. Dashboard, APIs, plugins, WebSockets, and any authentication proxy in front
of it keep working, and no account is created for you.

What is added is a security prompt on every page that cannot be dismissed. There is no
"later" and no "do not show again", because leaving the instance open is a decision, not
a state to postpone. It disappears when you take one of the two decisions:

- **Set internal sign-in up.** Follow the link in the prompt, or open
  **Settings → Sign-in & users**. The guided setup asks for the hostname and — if no
  administrator exists yet — the first admin account, using the same trusted HTTPS/443
  boundary as a new installation. If your installation still has an active administrator
  from an earlier activation, only the hostname is confirmed; no account is created,
  changed, or shown. Afterwards you sign in normally.
- **Stay open deliberately.** Set `OSD_AUTH_DISABLED=true` and restart; see
  [Deliberately running without internal sign-in](#deliberately-running-without-internal-sign-in).

An installation that already had internal sign-in enabled is marked as finished during
the update and shows neither the setup nor the prompt.

## Deliberately running without internal sign-in

You do not have to use internal sign-in. If a VPN, a network boundary, or an
authentication proxy already protects OpenSecDash, you can keep it open on purpose:

```yaml
services:
  opensecdash:
    environment:
      - OSD_AUTH_DISABLED=true # runs OpenSecDash without internal sign-in
```

This is the only way to bypass internal sign-in, and it is deliberately a container
setting rather than a button in the UI. Before you choose it, understand what it means:

- the variable is set in your Docker or Compose configuration and needs a restart;
- everyone who can reach OpenSecDash then has full access to all pages, APIs, and
  actions;
- internal roles, sign-in, sessions, and the authentication hostname boundary no longer
  protect the instance;
- you have to protect access yourself, with network boundaries or an external
  authentication proxy;
- an unfinished first-time setup stays open: removing the variable and restarting brings
  the setup page back, and a still-open installation gets its security prompt back;
- for permanently open operation the variable has to stay set permanently;
- while it is set, the warning about unprotected full access stays visible on every page.

Nothing stored changes while the variable is active. `auth.enabled`, the onboarding
state, users, password hashes, provider configuration, and links are all left alone, so
removing the variable restores the saved state exactly.

Internal sign-in cannot be switched off in Settings any more. The former "Disable
internal sign-in" button is gone, so an open instance is always either an explicit
`OSD_AUTH_DISABLED` decision or an installation that was never activated.

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
   has no option to skip certificate verification.

Provider connections use the container's own trust store, so a homelab provider with a
private CA works once that CA is trusted inside the container. With the hardened
read-only Compose example, mount a PEM bundle and point `SSL_CERT_FILE` at it:

```yaml
services:
  opensecdash:
    volumes:
      - ./homelab-ca.pem:/etc/opensecdash/ca-bundle.pem:ro
    environment:
      - SSL_CERT_FILE=/etc/opensecdash/ca-bundle.pem
```

`SSL_CERT_FILE` replaces the default certificate file, so put your private CA **and**
the public CAs you still need into that one bundle — for example by appending your CA to
a copy of the system bundle. Alternatively add the CA to the image's trust store in your
own derived image.

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

This is the same switch as
[Deliberately running without internal sign-in](#deliberately-running-without-internal-sign-in),
used temporarily. While it is set, every visitor who can reach OpenSecDash has the same
access as before internal sign-in was enabled, so restrict network access until you
remove it again.

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

The first-time setup uses no setup token, invitation code, or one-time password. Instead,
it grants nothing: the page is readable, but only a submission through the explicitly
trusted HTTPS/443 proxy boundary with the matching hostname is accepted, and the first
administrator, the hostname, and the finished state are written in one transaction.
Finishing it creates no session and sets no cookie, so the first sign-in goes through the
normal login with its throttling and hostname checks. Two concurrent submissions are
serialized in the database, so exactly one administrator is created. A setup that is
still open while accounts already exist is refused instead of adopting one of them.

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
