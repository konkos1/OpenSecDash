# Security model

OpenSecDash is designed as an internal homelab admin tool.

::: danger Protect integrations that can affect other systems
Do not use real CrowdSec Ban/Unban actions, Proxmox integration, or MQTT publishing unless both of these protections are in place:

1. OpenSecDash access requires either [internal sign-in](../configuration/authentication.md) or an external authentication provider such as Authentik, Authelia, or Pocket ID.
2. Users reach OpenSecDash exclusively through a [reverse proxy](../installation/reverse-proxy.md) using HTTPS and a certificate that their browsers trust.

A LAN-only deployment without authentication and HTTPS is not sufficient for these features. Anyone who can reach an unprotected dashboard can trigger actions, change integration settings, or obtain access to connected-system capabilities.
:::

## Internal sign-in

OpenSecDash includes internal sign-in with Viewer, Operator, and Admin roles. A new installation starts with it enabled and asks the first visitor to create the first administrator. An installation that is updated keeps whatever state it had: enabled stays enabled, open stays open.

Treat the dashboard as sensitive whenever internal sign-in is not active. Two permanent, non-dismissible notices make that state visible: an installation that stayed open across an update asks you to decide between internal sign-in and a deliberate bypass, and an instance running with `OSD_AUTH_DISABLED=true` shows the stronger warning that every visitor who can reach it has full Viewer, Operator, and Admin access.

`OSD_AUTH_DISABLED=true` is the only way to bypass internal sign-in, and it is a container setting on purpose — there is no button for it in the UI. Use it when a VPN, a network boundary, or a forward-auth layer already protects the instance, and keep it set for as long as you want that.

Recommended placement:

- LAN-only
- VPN-only
- behind Authentik, Authelia, Pocket ID, or another trusted forward-auth layer

Do not expose it directly to the public internet.

Using Authentik, Authelia, Pocket ID, or another trusted forward-auth layer without also enabling OpenSecDash's internal sign-in is a supported homelab setup. This avoids a second password prompt while keeping access protected at the reverse proxy.

See [Authentication](../configuration/authentication.md) to enable internal sign-in and recover from an administrator lockout.

## Forward auth in front vs. built-in single sign-on

Both setups can use the same identity provider, but they protect different things:

- **Forward auth in front of the proxy** (Authentik, Authelia, Pocket ID, …): the proxy decides who reaches OpenSecDash at all. OpenSecDash itself sees an already authorized visitor and, with internal sign-in disabled, treats everyone as an Admin. There are no per-user roles, no personal preferences, and no per-user audit trail.
- **Built-in single sign-on**: internal sign-in is enabled and uses the provider as an additional sign-in method. Every user gets a local account with its own role, and provider accounts are matched only by the provider's immutable subject.

Use built-in single sign-on when different people should have different roles. Keep the forward-auth layer in front when you only want to keep unauthorized visitors away, and combine both when you want the outer gate plus local roles. Roles always come from the local user list — OpenSecDash reads no group or role claims from the provider.

## Trusted reverse proxies

Forwarded client IP, scheme, and host headers are accepted only from configured trusted proxy addresses. This prevents an arbitrary direct client from spoofing `X-Forwarded-For` or making an HTTP request appear to be HTTPS.

The default trust set covers typical loopback and private homelab networks. Narrow or disable it with `OSD_TRUSTED_PROXIES` when your topology requires a stricter boundary. See the [reverse proxy guide](../installation/reverse-proxy.md).

## Sensitive data

OpenSecDash can display:

- public and internal IP addresses
- hostnames
- asset names
- access logs
- security events
- action controls such as CrowdSec ban/unban

Review debug reports before attaching them to public issues.

SMTP notifications intentionally send matching event or Insight details to the mail server configured by the administrator. Leave notifications disabled if that destination is not trusted. The Insights rule updater only downloads declarative JSON rules from fixed OpenSecDash website URLs; it does not upload local events, IPs, hostnames, or telemetry. A fixed, expiring SHA-256 manifest is verified before remote rules are stored; see the [Insights engine guide](../operations/insight-rules.md) for the remaining same-site trust limitation.

### Remote GeoIP

Remote GeoIP stays disabled until an administrator enables it, and it never sends private, local, reserved, or otherwise non-public addresses. Successful lookups are cached for the configured TTL, failures for one hour.
Two providers with clearly different transports can be selected; new installations pre-select IPLocate, and an existing installation keeps whatever it already stored.

- **IPLocate** sends each uncached public IP over HTTPS to the provider's fixed EU endpoint. The endpoint is a constant rather than a setting, certificate verification is always on, and redirects are not followed. The required API key is stored encrypted, travels in the `X-API-Key` request header only, and appears neither in URLs nor in HTML, logs, or debug reports. TLS protects the transport only: IPLocate still receives and processes the public IP being looked up. Responses are size-capped and limited to the country, city, ASN, and company/hosting fields OpenSecDash stores.
- **ip-api.com** remains available as a legacy option and sends each uncached public IP over **unencrypted HTTP**, readable by anyone on the network path. A failing IPLocate lookup never falls back to it.

See the [GeoIP plugin guide](../plugins/geoip.md).

### GeoIP as security-relevant input

Permanent ASN policies make the stored GeoIP classification security-relevant. ASN, ASN-organization, and ISP/company fields can be wrong, stale, incomplete, or supplied by the event producer; an ASN can be transferred and an organization name can change.
OpenSecDash keeps the ASN organization separate from the IP-specific ISP/company value, treats it as a display snapshot rather than authoritative ASN identity, and confirms a substantial name change through three observations across at least two IPs before marking it for review. Neither detecting nor acknowledging a changed name automatically releases a decision.

This workflow adds no new external destination: it uses the already selected GeoIP provider and CrowdSec LAPI. Before activation, OpenSecDash re-reads the server-side source event, requires completed GeoIP enrichment, normalizes the ASN, and validates the IP as globally routable. Policy enforcement and release use the exact stored CrowdSec decision ID, scenario, and origin; they never broadly unban an IP or remove an independent CrowdSec decision. ASN organization names are inserted into the UI as text, not HTML.

::: warning Response starts only after observation
The permanent ASN workflow cannot protect the request that supplies its classification:

```text
first access reaches the service
→ event is stored
→ GeoIP enriches it asynchronously
→ OpenSecDash creates a seven-day CrowdSec IP decision
→ the bouncer fetches and applies the decision
```

It can therefore block **no earlier than the second access**. This is not a guarantee; provider, enrichment, LAPI, or bouncer latency and errors can allow further accesses.
:::

## Remote reads and input boundaries

JSON Assets URL sources may use HTTP or HTTPS and may point into private IPv4 or IPv6 homelab networks. OpenSecDash rejects URL credentials, loopback, unspecified, multicast, link-local, and known cloud metadata targets. It validates every DNS answer and every redirect target, follows at most three redirects, ignores proxy environment variables, and uses separate connect/read timeouts. DNS can change between validation and connection, so this policy reduces but cannot completely eliminate DNS-rebinding risk.

Remote and local `assets.json` input is limited to 10 MiB, JSON depth 20, 10,000 systems, 1,000 apps per system, and 2,048 characters per field. Remote compressed and unpacked sizes are both checked. The API and all other writing routes also have server-side body limits; the global default is 12 MiB and can be lowered with `MAX_REQUEST_BODY_BYTES`. Event API fields follow the database field sizes, `data_json` and `raw_data` are each limited to 1 MiB, and JSON depth is limited to 20.
Remote JSON responses are bounded as well: 64 KiB for GeoIP and CrowdSec login, 256 KiB for GitHub release checks, 4 MiB per Proxmox request, and 8 MiB for CrowdSec alerts. These responses are streamed and rejected before JSON decoding when either their declared or observed size exceeds the limit.

## Action simulation

Action simulation / dry-run mode is the safer default. It records actions without executing them. Disable dry-run only after you have verified plugin configuration and permissions.

## Action controls

Critical actions such as CrowdSec ban and unban require confirmation. OpenSecDash centrally rejects private, local, and otherwise non-global IP targets before a critical IP action reaches a plugin. Action simulation is enabled by default, so buttons can be tested without changing CrowdSec; disable **Action simulation** in Settings only when the plugin connection and permissions are ready.

Results are recorded in Diagnostics under **Recent actions** and in the Events view. Generic actions emit `action.executed` or `action.failed`; CrowdSec uses its existing specific success event types for completed Ban/Unban actions and the shared `action.failed` event for failures.

See [Actions and safety](../operations/actions.md) for the full execution and audit flow.

## Browser protections

OpenSecDash applies one global browser-header policy to normal pages, login and error pages, APIs, static assets, the service worker, and uploaded instance images. Framing is blocked by both CSP `frame-ancestors 'none'` and `X-Frame-Options: DENY`. Scripts are restricted to local files. Alpine expressions currently require CSP `unsafe-eval`, and Alpine/HTMX visibility and indicator styles require `unsafe-inline` for styles; no foreign script CDN or wildcard source is allowed.

## CrowdSec connection security

OpenSecDash communicates with CrowdSec exclusively through LAPI. It does not mount or launch a configurable `cscli` executable. This removes an unnecessary subprocess/code-execution primitive and avoids mounting CrowdSec configuration into the application container.

Use dedicated, revocable CrowdSec machine credentials. LAPI URLs are validated, embedded credentials and query strings are rejected, and authenticated requests do not follow redirects. See [CrowdSec](../plugins/crowdsec.md).
