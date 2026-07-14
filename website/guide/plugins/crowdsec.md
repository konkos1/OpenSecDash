# CrowdSec Plugin

The CrowdSec plugin imports CrowdSec ban history, synchronizes active decisions, and executes ban/unban actions through CrowdSec's Local API (LAPI).

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

## Connection diagnostics

The CrowdSec page and IP Explorer show LAPI reachability and authentication status. In dry-run mode, connection errors are not shown as prominent action errors because real actions are not executed.

Diagnostics separates the two CrowdSec responsibilities:

- `plugin · crowdsec` reports whether the configured `crowdsec.log` datasource is available.
- `crowdsec · lapi` reports LAPI authentication and active-decision synchronization.
