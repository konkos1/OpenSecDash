# CrowdSec Plugin

The CrowdSec plugin imports CrowdSec ban history, synchronizes active decisions, and executes ban/unban actions through CrowdSec's Local API (LAPI).

::: danger Protect Ban and Unban
Do not disable action simulation or use real Ban/Unban unless OpenSecDash requires either [internal sign-in](../configuration/authentication.md) or an external authentication provider and is accessed exclusively through an HTTPS [reverse proxy](../installation/reverse-proxy.md) with a browser-trusted certificate.
An unauthenticated LAN dashboard allows every reachable client to operate CrowdSec with OpenSecDash's machine credentials.
:::

::: danger Breaking change for former `cscli` mode
OpenSecDash no longer mounts or executes `cscli`. Existing connection-mode and executable-path settings are ignored. Configure an LAPI URL and dedicated CrowdSec machine credentials after upgrading.
:::

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables CrowdSec log import and CrowdSec actions. |
| CrowdSec log path | Path to `crowdsec.log`. In Docker, mount the host log read-only into the container. Ban history, scenarios, and countries are derived from matching log lines. |
| LAPI URL | Base URL of the CrowdSec Local API, e.g. `http://127.0.0.1:8080` with host networking. |
| LAPI login / LAPI password | The machine credentials created for OpenSecDash (see below). The password is stored encrypted. |
| CrowdSec poll interval seconds | How often the CrowdSec log is checked for appended ban history entries. |

OpenSecDash also synchronizes active CrowdSec decisions for the Unban workflow.

## Typical setup

In Docker, mount the CrowdSec log read-only into the OpenSecDash container and configure the plugin path in Settings.

```yaml
volumes:
  - /var/log/crowdsec.log:/logs/crowdsec.log:ro
```

The plugin defaults already assume this layout: `CrowdSec log path` defaults to `/logs/crowdsec.log`.

If the mounted file already has a lot of history, the first import can take a while; see [Troubleshooting: first import of a large existing log](../operations/troubleshooting.md#first-import-of-a-large-existing-log).

## Connecting via the Local API

The LAPI is the local REST API every CrowdSec installation runs (by default on `127.0.0.1:8080`) - it is part of the free open-source CrowdSec, not a paid feature. OpenSecDash needs no CrowdSec binary or config files inside its container. Setup is three steps:

**1. Create dedicated credentials on the CrowdSec host:**

```bash
sudo cscli machines add opensecdash --auto -f /tmp/opensecdash-lapi.yaml
sudo cat /tmp/opensecdash-lapi.yaml
sudo rm /tmp/opensecdash-lapi.yaml
```

::: warning
Always pass `-f <file>`: without it, `cscli machines add` may overwrite `/etc/crowdsec/local_api_credentials.yaml` - the credentials CrowdSec itself uses.
:::

The printed file contains the three values you need: `url`, `login`, and `password`.

**2. Enter them in OpenSecDash** under Settings → CrowdSec: fill in LAPI URL, login, and password. The password is stored encrypted at rest.

**3. Make sure the LAPI is reachable from the container.** It listens on `127.0.0.1:8080` by default, which is not reachable from a normal Docker bridge network. `network_mode: "host"` is the simplest fix when CrowdSec runs on the same host/LXC - the container then reaches the LAPI on `127.0.0.1` directly without exposing it further. If CrowdSec runs in its own container instead, use that container's LAPI URL on the shared Docker network.

Why this is also the safer option: the credentials belong to a dedicated, revocable machine account. If they ever leak, `sudo cscli machines delete opensecdash` on the CrowdSec host invalidates them immediately - the host's own CrowdSec credentials never leave `/etc/crowdsec`.

The `cscli` commands in this guide run only on the CrowdSec host to create or revoke LAPI credentials. OpenSecDash itself does not execute `cscli`.

The LAPI URL must use `http://` or `https://`, include a host, and must not contain embedded credentials, a query, or a fragment. OpenSecDash does not follow redirects for LAPI requests, so credentials and bearer tokens stay on the configured endpoint.

## Why the subprocess integration was removed

The previous integration could launch a configurable `cscli` path inside the OpenSecDash container. That required mounting a host executable and CrowdSec configuration into the container. It also created an unnecessary code-execution primitive if an attacker gained settings access together with a way to place or select a suitable executable.

LAPI exposes all functionality OpenSecDash needs without host-binary or CrowdSec-configuration mounts. Its machine credentials are dedicated to OpenSecDash, can be revoked independently, and are stored encrypted at rest. The LAPI client also rejects embedded URL credentials, query strings, fragments, invalid ports, and redirects.

## Actions and dry run

OpenSecDash has an action simulation mode. While dry run is enabled, ban/unban actions are recorded but not executed.

When dry run is disabled, unban buttons are shown only when OpenSecDash knows about an active CrowdSec ban decision. Decisions are synchronized from the LAPI.

See [Actions and safety](../operations/actions.md) for central target validation, permissions, confirmations, and audit history.

## Permanent manual ASN bans

OpenSecDash can keep a permanent local policy for an unwanted ASN and respond to newly observed matching IPs. This requires all of the following:

- the CrowdSec plugin enabled, with an authenticated and reachable LAPI;
- [GeoIP](./geoip.md#geoip-and-permanent-asn-bans) enabled and usable;
- Action simulation disabled for real activation and enforcement;
- an Operator or Admin for policy actions.

In Events or Access, open **Columns** and enable the optional **ASN** column if it is hidden. The column shows the ASN number and its organization. Select the value to open its popup, review the ASN organization snapshot, and confirm **Permanently ban ASN**. Activation bans only the popup's current public IP; it does not scan older events for other IPs.

### What CrowdSec receives

For each newly observed public IP that a successful GeoIP enrichment assigns to the policy ASN, OpenSecDash sends a normal global decision with:

```text
scope=Ip
duration=7d
origin=opensecdash
scenario=opensecdash/manual-permanent-asn-ban/AS...
```

OpenSecDash explicitly does **not** use `scope=As`. It neither resolves the ASN to BGP prefixes nor depends on ASN-aware bouncers; standard bouncers only need their normal IP decision support. The local ASN policy is permanent, but each CrowdSec decision is not.

An existing active CrowdSec ban for the IP prevents a duplicate policy decision. When a policy decision expires, no timer renews it. Only a later newly stored and enriched event can create another seven-day decision. If that event maps the IP to a different ASN:

- a new non-blocked ASN releases only the exact old policy-owned decision ID;
- an independent decision for the same IP remains untouched;
- a failed release stays `release_pending` for an ID-specific retry during a later CrowdSec tick;
- if the new ASN is also blocked, the existing decision remains attributed to the old policy until expiry; ownership is never transferred in place.

### Exceptions and removal

Manually unbanning a policy-owned decision automatically creates a durable exception for that ASN/IP pair after the LAPI deletion succeeds. It is not a global allowlist, and there is no ambiguous one-time unban. A failed or simulated unban creates no exception; an independent CrowdSec unban follows the existing flow and creates none.

Removing an exception is confirmed and re-enables future matching for that pair, but old events are not reprocessed. Removing an ASN policy is also confirmed and deletes only active decisions whose exact ID, scenario, origin, IP, and ownership record still match.
A partial failure leaves the policy in `removing` with its error visible and retryable.

The CrowdSec page manages the policies, latest ASN organization snapshots, active policy-owned decisions, exceptions, pending releases, and removal failures. Existing policies and exceptions remain visible if GeoIP or CrowdSec is disabled; new enforcement pauses.

### Insights, counters, and scenario history

Every successful automatic policy ban creates one high-confidence IP Explorer insight that records the ASN, organization snapshot, and `7d` duration. Its wording is historical: it says that the IP **was banned**, not that its decision is still active. Current state comes from the CrowdSec active-ban panel.

Policy bans count in ban totals, IP Explorer counts, CrowdSec history, active decisions, rollups, and the Dashboard. The complete ASN-specific scenario stays visible in Events and CrowdSec history. Rollups, CrowdSec top scenarios, and the Dashboard combine all of them under the localized **Manual permanent ASN ban** group; its drill-down searches the complete scenarios and therefore finds each contributing ASN.

### ASN-organization review

The ASN organization name is a mutable GeoIP display snapshot, not ASN identity. It is stored separately from the IP-specific ISP/company value. A substantially different non-empty name must appear in three matching observations from at least two IPs before OpenSecDash keeps the previous and current snapshots plus the detection time and shows **ASN organization changed – review required**. Unicode, case, whitespace, and common trailing legal-form variants are normalized conservatively; other text remains significant. Returning to the current name resets the candidate, and an already open warning is not emitted repeatedly. Acknowledging the warning confirms only that the latest snapshot was reviewed; it does not prove an ownership change, pause enforcement, or remove a policy or decision. Policy removal remains a separate confirmed action.

::: warning The first access is always observed before enforcement
The sequence is:

```text
first access reaches the service
→ event is stored
→ GeoIP enriches it asynchronously
→ OpenSecDash creates the 7d IP decision
→ the CrowdSec bouncer fetches and applies it
```

Blocking is therefore possible **no earlier than the second access**, and even that is not guaranteed. GeoIP, LAPI, and bouncer latency or errors can allow further accesses.
:::

## Connection diagnostics

The CrowdSec page and IP Explorer show LAPI reachability and authentication status. In dry-run mode, connection errors are not shown as prominent action errors because real actions are not executed.

Diagnostics separates the two CrowdSec responsibilities:

- `plugin · crowdsec` reports whether the configured `crowdsec.log` datasource is available.
- `crowdsec · lapi` reports LAPI authentication and active-decision synchronization.
