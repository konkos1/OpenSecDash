# Troubleshooting

## Start with Diagnostics

Open the Diagnostics page and check:

- plugin status
- datasource status
- database migration status
- recent actions

## Create a debug report

Use **Diagnostics → Download debug report** and review the ZIP before sharing it.

## Docker logs

```bash
docker compose logs opensecdash --tail=500
```

## Health checks

`GET /health` is the liveness check. It only confirms that the application process can answer HTTP and does not access the database.

`GET /ready` is the readiness check. After startup has completed, it performs one read-only `SELECT 1` database ping. It does not run migrations, seed defaults, rotate secrets, or perform event maintenance. A database error returns `503 Service Unavailable` without database paths or exception details.

## A setup page appears instead of the dashboard

A new installation starts with internal sign-in enabled and asks for the first
administrator once. Until that is done, every page redirects to the setup, APIs answer
`503`, and the event WebSocket is closed; `/health` and `/ready` keep working. This is
expected — finish the setup, or run OpenSecDash open on purpose with
`OSD_AUTH_DISABLED=true`.

An updated installation never shows this page. If it appears after an update, the
database was replaced or is empty, not migrated.

## The setup cannot be completed

The form can be filled in from anywhere, but it is only accepted through the trusted
HTTPS/443 proxy boundary. The **Connection requirements** section on the page shows which
check fails. Usual causes:

| What the page reports | Usual cause |
| --- | --- |
| The proxy is not trusted | `OSD_TRUSTED_PROXIES` is unset, still on the defaults, or `*`. Name the proxy IP or a small dedicated CIDR explicitly. |
| HTTPS is required | The proxy does not send `X-Forwarded-Proto: https`, or you opened OpenSecDash directly over HTTP. |
| Port 443 is required | The proxy does not send `X-Forwarded-Port: 443`, or it publishes OpenSecDash on a different external port. |
| The hostname does not match | The hostname in the form differs from `X-Forwarded-Host`. Enter it without `https://`, a port, a path, or a trailing dot. |

See [Reverse proxy](../installation/reverse-proxy.md) for the proxy settings and
[Authentication](../configuration/authentication.md#prepare-the-reverse-proxy-first) for
the full list.

If `OSD_AUTH_DISABLED` is set, the setup deliberately cannot be completed at all. Remove
the variable and restart, and the setup continues where it left off.

## A prompt asks to decide how the installation is protected

An installation that was still open when it was updated keeps working exactly as before —
pages, APIs, plugins, WebSockets, and any authentication proxy in front of it. The prompt
cannot be dismissed because staying open is a decision. Either set internal sign-in up
through the link in the prompt, or set `OSD_AUTH_DISABLED=true` and restart. See
[Updated installations that are still open](../configuration/authentication.md#updated-installations-that-are-still-open).

## Internal sign-in cannot be switched off in Settings

That is intentional. `OSD_AUTH_DISABLED=true` plus a restart is the only way to bypass
internal sign-in; see
[Deliberately running without internal sign-in](../configuration/authentication.md#deliberately-running-without-internal-sign-in).
Removing the variable restores the stored state, including a setup that is still open.

## Locked out of the web UI

If internal sign-in is enabled and no administrator can sign in, use the
`OSD_AUTH_DISABLED=true` recovery switch to temporarily open the UI, reset access, or
repair a changed authentication hostname. Restrict network access while the switch is
active: every client that can reach OpenSecDash has full access. Remove the variable and
restart after the repair. See
[Authentication](../configuration/authentication.md#locked-out-of-the-web-ui).

## Single sign-on does not work

Sign-in errors are intentionally generic in the browser. The container log carries the
error class, and **Diagnostics → Single sign-on (OIDC)** shows the stored result of the
last provider check without contacting the provider.

| What you see | Usual cause |
| --- | --- |
| "Single sign-on is not available right now." | The provider is not enabled or not completely configured, the authentication hostname is missing, or the request did not arrive through the trusted HTTPS/443 proxy boundary. |
| "The sign-in took too long. Please start again." | The ten-minute transaction cookie expired, the container restarted mid-sign-in, the browser dropped the cookie, or the same provider response was replayed. |
| "Signing in with the provider did not work." | The provider was unreachable, its TLS certificate is not trusted inside the container, the token exchange failed, or the ID token was rejected — wrong issuer, wrong audience, wrong or missing nonce, expired token, or an unsafe signature algorithm. |
| "This provider account cannot sign in to OpenSecDash." | The provider account is not linked and automatic user creation is off, or the linked local user is deactivated. |
| The provider itself refuses with a redirect URI error | The registered redirect URL does not match `https://<your hostname>/auth/oidc/callback`. |

Check in this order:

1. Does **Settings → Sign-in & users** report a successful last provider check? Use
   **Check and save provider** to repeat it.
2. Does the registered redirect URL match the value shown in Settings exactly?
3. Can the container reach the provider and trust its certificate? Provider connections
   use the container's trust store, so a private CA has to be trusted there — mount a
   PEM bundle and set `SSL_CERT_FILE`, or add the CA to your own derived image.
   OpenSecDash has no option to skip certificate verification.
4. Is the discovery URL an HTTPS address without credentials, query, or fragment, and
   does it live on the same host as the issuer it declares? Loopback and cloud metadata
   addresses are rejected on purpose; private homelab addresses are allowed.
5. Did the issuer change at the provider? A changed issuer needs a new provider check,
   and existing links stay with the old issuer.
6. Is the browser reaching OpenSecDash under the configured hostname over HTTPS on port
   443? A different hostname or a missing forwarded header stops sign-in before any
   request leaves the container.

If nobody can sign in because password sign-in is off, use the emergency switch
described under
[Provider outage or misconfiguration](../configuration/authentication.md#provider-outage-or-misconfiguration).

## First import of a large existing log

Setting a log path (or enabling a log-based plugin) for the first time makes OpenSecDash read through the whole existing file, not just new lines going forward. A small/fresh log finishes almost instantly; a log that already has months of history can take a while to fully import. That import runs in bounded batches in the background instead of all at once, so the UI stays responsive and usable while it catches up.

A banner near the top of every page shows while a plugin is still catching up on a backlog, with a rough progress percentage, and disappears automatically once it reaches the end of the file. GeoIP country/city/ASN/ISP lookups for the imported events are filled in afterwards at their own pace, so they may briefly show as unknown right after a large first import.

## Permanent ASN ban troubleshooting

### The ASN action is missing or unavailable

Use **Columns** on Events or Access to show the optional **ASN** column first.
The action also requires an Operator or Admin, a public source IP, a stored ASN from a
completed GeoIP enrichment, enabled and healthy GeoIP, enabled CrowdSec with a healthy
LAPI connection, and Action simulation turned off. The popup explains the first failing
prerequisite; Diagnostics shows the GeoIP and `crowdsec · lapi` details.

### A policy is active but an IP was not banned

The first event may still await GeoIP enrichment, enrichment may have failed, or the IP
may have an exception for this policy. OpenSecDash also does not create a second policy
decision while any active CrowdSec ban decision for that IP is already known. Check LAPI
reachability and allow for bouncer propagation latency. Expired policy decisions are not
renewed by a timer; another matching enriched event is required.

### An IP is banned despite an exception

An exception is scoped to one ASN policy and IP. Check the active decision's scenario and
origin: a different blocked ASN or an independent CrowdSec decision can still ban the IP.

### An IP remains policy-banned after its ASN changed

On the CrowdSec policy card, look for a pending release and its exact decision ID, then
check `crowdsec · lapi` in Diagnostics. A failed release stays `release_pending` and is
retried only for that stored ID. Do not use a broad IP unban: another decision for the
same IP may be independent and must remain untouched. If the new ASN is also permanently
blocked, the existing decision deliberately stays owned by the previous policy until it
expires.

### A policy remains in `removing`

Policy removal stops new matches before deleting its active, exactly owned decisions.
Inspect the displayed removal error and LAPI diagnostic, restore connectivity, then use
the retry action. OpenSecDash leaves a partial removal visible instead of deleting
unverified or foreign decisions.

### The ASN or ASN organization is unexpected

Review the selected GeoIP provider, its latest real-lookup diagnostic, cache TTL, and any
recent provider switch. Producer-supplied event fields win over remote enrichment, and
external data can be stale, incomplete, or wrong. ASN organization is stored separately
from the IP-specific ISP/company value and is not an authoritative registry identity.

If **ASN organization changed – review required** appears, compare the previous and
current snapshots and detection time, the GeoIP source, and a possible rename or ASN
transfer. OpenSecDash only raises it after three matching observations across at least
two IPs and suppresses duplicates while it is open. The warning still does not prove
ownership changed and does not pause or remove the policy. After review, either
acknowledge only the warning or remove the ASN policy with the separate confirmed action.
A newer confirmed organization change makes an older acknowledgement stale and requires
another review.

## Proxmox guest visibility

If the Proxmox plugin imports nodes but no guests, check the plugin diagnostic message and verify the API token can see `qemu` or `lxc` entries from:

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=opensecdash@pve!inventory=TOKEN_SECRET' \
  'https://pve.example.local:8006/api2/json/cluster/resources'
```
