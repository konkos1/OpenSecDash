# CrowdSec Plugin

The CrowdSec plugin imports CrowdSec ban history and can execute ban/unban actions through `cscli`.

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables CrowdSec log import and CrowdSec actions. |
| CrowdSec log path | Path to `crowdsec.log`. In Docker, mount the host log read-only into the container. Ban history, scenarios, and countries are derived from matching log lines. |
| cscli path | Command or absolute path used for active decisions and ban/unban actions. |
| CrowdSec poll interval seconds | How often the CrowdSec log is checked for appended ban history entries. |

OpenSecDash also synchronizes active CrowdSec decisions for the Unban workflow.

## Typical setup

In Docker, mount the CrowdSec log read-only into the OpenSecDash container and configure the plugin path in Settings.

```yaml
volumes:
  - /var/log/crowdsec.log:/logs/crowdsec.log:ro
```

The plugin defaults already assume this layout: `CrowdSec log path` defaults to `/logs/crowdsec.log` and `cscli path` defaults to `/usr/local/bin/cscli`.

## cscli in Docker

Ban/unban actions and active decision sync run `cscli` as a real subprocess inside the OpenSecDash container, not through the CrowdSec API. If CrowdSec itself runs on the Docker host (or the LXC/VM hosting it) rather than in its own container, `cscli` and its credentials need to be made available to OpenSecDash's container:

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
- **CrowdSec config**: bind-mount `/etc/crowdsec` read-only so `cscli` finds `local_api_credentials.yaml` and can authenticate against the CrowdSec Local API (LAPI). Make sure `local_api_credentials.yaml` is readable by the unprivileged `opensecdash` user the container runs as.
- **Network reachability**: the LAPI listens on `127.0.0.1:8080` by default, which is not reachable from a normal Docker bridge network. `network_mode: host` is the simplest fix for same-host setups, since it lets the container reach the LAPI on `127.0.0.1` directly without exposing it further. If host networking is not an option, point the LAPI at a reachable address instead (for example the Docker bridge gateway) and restrict access with the host firewall.

If CrowdSec instead runs in its own container with the LAPI exposed on the Docker network, point `cscli path` at a wrapper script or sidecar that runs `cscli` against that container's LAPI URL, since there is no `cscli` binary to bind-mount from the host in that case.

## Actions and dry run

OpenSecDash has an action simulation mode. While dry run is enabled, ban/unban actions are recorded but not executed.

When dry run is disabled, unban buttons are shown only when OpenSecDash knows about an active CrowdSec ban decision. Decisions are synchronized from `cscli decisions list -o json`.

## cscli diagnostics

The CrowdSec page and IP Explorer show `cscli` status. In dry-run mode, `cscli` errors are not shown as prominent action errors because real actions are not executed.
