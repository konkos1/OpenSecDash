# Security model

OpenSecDash is designed as an internal homelab admin tool.

::: danger Protect integrations that can affect other systems
Do not use real CrowdSec Ban/Unban actions, Proxmox integration, or MQTT publishing
unless both of these protections are in place:

1. OpenSecDash access requires either [internal sign-in](../configuration/authentication.md)
   or an external authentication provider such as Authentik, Authelia, or Pocket ID.
2. Users reach OpenSecDash exclusively through a
   [reverse proxy](../installation/reverse-proxy.md) using HTTPS and a certificate that
   their browsers trust.

A LAN-only deployment without authentication and HTTPS is not sufficient for these
features. Anyone who can reach an unprotected dashboard can trigger actions, change
integration settings, or obtain access to connected-system capabilities.
:::

## Optional internal sign-in

OpenSecDash includes optional internal sign-in with Viewer, Operator, and Admin roles.
It is disabled by default, so treat the dashboard as sensitive even when internal
sign-in is not enabled.

Recommended placement:

- LAN-only
- VPN-only
- behind Authentik, Authelia, Pocket ID, or another trusted forward-auth layer

Do not expose it directly to the public internet.

See [Authentication](../configuration/authentication.md) to enable internal sign-in and
recover from an administrator lockout.

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

SMTP notifications intentionally send matching event or Insight details to the mail server configured by the administrator. Leave notifications disabled if that destination is not trusted. Remote GeoIP is disabled by default; when enabled, it sends each uncached public IP over unencrypted HTTP to `ip-api.com`, caches successful results for the configured TTL and failures for one hour, and never sends private or reserved IPs. The Insights rule updater only downloads declarative JSON rules from the fixed OpenSecDash website URL; it does not upload local events, IPs, hostnames, or telemetry.

## Remote reads and input boundaries

JSON Assets URL sources may use HTTP or HTTPS and may point into private IPv4 or IPv6
homelab networks. OpenSecDash rejects URL credentials, loopback, unspecified,
multicast, link-local, and known cloud metadata targets. It validates every DNS answer
and every redirect target, follows at most three redirects, ignores proxy environment
variables, and uses separate connect/read timeouts. DNS can change between validation
and connection, so this policy reduces but cannot completely eliminate DNS-rebinding
risk.

Remote and local `assets.json` input is limited to 10 MiB, JSON depth 20, 10,000
systems, 1,000 apps per system, and 2,048 characters per field. Remote compressed and
unpacked sizes are both checked. The API and all other writing routes also have
server-side body limits; the global default is 12 MiB and can be lowered with
`MAX_REQUEST_BODY_BYTES`. Event API fields follow the database field sizes,
`data_json` and `raw_data` are each limited to 1 MiB, and JSON depth is limited to 20.

## Action simulation

Action simulation / dry-run mode is the safer default. It records actions without executing them. Disable dry-run only after you have verified plugin configuration and permissions.

## Action controls

Critical actions such as CrowdSec ban and unban require confirmation. OpenSecDash centrally rejects private, local, and otherwise non-global IP targets before a critical IP action reaches a plugin. Action simulation is enabled by default, so buttons can be tested without changing CrowdSec; disable **Action simulation** in Settings only when the plugin connection and permissions are ready.

Results are recorded in Diagnostics under **Recent actions** and in the Events view. Generic actions emit `action.executed` or `action.failed`; CrowdSec uses its existing specific success event types for completed Ban/Unban actions and the shared `action.failed` event for failures.

See [Actions and safety](../operations/actions.md) for the full execution and audit flow.

## CrowdSec connection security

OpenSecDash communicates with CrowdSec exclusively through LAPI. It does not mount or launch a configurable `cscli` executable. This removes an unnecessary subprocess/code-execution primitive and avoids mounting CrowdSec configuration into the application container.

Use dedicated, revocable CrowdSec machine credentials. LAPI URLs are validated, embedded credentials and query strings are rejected, and authenticated requests do not follow redirects. See [CrowdSec](../plugins/crowdsec.md).
