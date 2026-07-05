# CrowdSec Plugin

The CrowdSec plugin imports CrowdSec ban history and can synchronize active decisions and execute ban/unban actions - preferably over CrowdSec's Local API (LAPI), or alternatively through the `cscli` binary.

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables CrowdSec log import and CrowdSec actions. |
| CrowdSec log path | Path to `crowdsec.log`. In Docker, mount the host log read-only into the container. Ban history, scenarios, and countries are derived from matching log lines. |
| Connection to CrowdSec | `Local API (recommended)` talks to CrowdSec over HTTP with dedicated credentials; `cscli binary` runs `cscli` as a subprocess instead. |
| LAPI URL | Base URL of the CrowdSec Local API, e.g. `http://127.0.0.1:8080` with host networking. |
| LAPI login / LAPI password | The machine credentials created for OpenSecDash (see below). The password is stored encrypted. |
| cscli path | Only for the `cscli binary` mode: command or absolute path used for active decisions and ban/unban actions. |
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

## Connecting via the Local API (recommended)

The LAPI is the local REST API every CrowdSec installation runs (by default on `127.0.0.1:8080`) - it is part of the free open-source CrowdSec, not a paid feature. Connecting through it needs no `cscli` binary and no CrowdSec config files inside the OpenSecDash container. Setup is three steps:

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

**2. Enter them in OpenSecDash** under Settings → CrowdSec: set `Connection to CrowdSec` to `Local API`, then fill in LAPI URL, login, and password. The password is stored encrypted at rest.

**3. Make sure the LAPI is reachable from the container.** It listens on `127.0.0.1:8080` by default, which is not reachable from a normal Docker bridge network. `network_mode: "host"` is the simplest fix when CrowdSec runs on the same host/LXC - the container then reaches the LAPI on `127.0.0.1` directly without exposing it further. If CrowdSec runs in its own container instead, use that container's LAPI URL on the shared Docker network - this mode needs no binary from the host at all.

Why this is also the safer option: the credentials belong to a dedicated, revocable machine account. If they ever leak, `sudo cscli machines delete opensecdash` on the CrowdSec host invalidates them immediately - the host's own CrowdSec credentials never leave `/etc/crowdsec`.

## Alternative: cscli binary in Docker

If you prefer the subprocess route (or already have it set up), ban/unban actions and decision sync can run `cscli` inside the OpenSecDash container. `cscli` and its credentials then need to be made available:

```yaml
services:
  opensecdash:
    image: konkos1/opensecdash:latest
    network_mode: "host"
    volumes:
      - opensecdash-data:/data
      - /usr/bin/cscli:/usr/local/bin/cscli:ro
      - /etc/crowdsec:/etc/crowdsec:ro
      - /var/log/crowdsec/crowdsec.log:/logs/crowdsec.log:ro
```

- **`cscli` binary**: bind-mount the host's `cscli` binary read-only into the container at `/usr/local/bin/cscli` (the plugin's default `cscli path`). It is a statically linked binary, so mounting it into the Python-based image works without library compatibility issues.
- **CrowdSec config**: bind-mount `/etc/crowdsec` read-only so `cscli` finds `local_api_credentials.yaml` and can authenticate against the LAPI. Make sure `local_api_credentials.yaml` is readable by the unprivileged `opensecdash` user the container runs as - this hands the host's own CrowdSec credentials to the container, which is exactly what the Local API mode avoids.
- **Network reachability**: same as above; `network_mode: host` is the simplest fix for same-host setups.

## Actions and dry run

OpenSecDash has an action simulation mode. While dry run is enabled, ban/unban actions are recorded but not executed.

When dry run is disabled, unban buttons are shown only when OpenSecDash knows about an active CrowdSec ban decision. Decisions are synchronized from the LAPI (or `cscli decisions list -o json` in cscli mode).

## Connection diagnostics

The CrowdSec page and IP Explorer show the connection status (LAPI reachability/authentication, or `cscli` availability). In dry-run mode, connection errors are not shown as prominent action errors because real actions are not executed.
