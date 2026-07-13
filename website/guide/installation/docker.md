# Docker Compose installation

Docker Compose is the recommended installation method.

## Host requirements

Minimum for a small homelab instance:

| Resource | Minimum | Recommended |
| --- | --- | --- |
| CPU | 1 vCPU | 2 vCPU |
| RAM | 512 MB | 1 GB+ |
| Storage | 1 GB free | SSD with several GB free, depending on log volume and retention |

OpenSecDash is lightweight, but storage usage depends on imported event volume, configured retention, and debug/log output.

As a rough guide, measured on SQLite after `VACUUM` (events plus their indexes and rollups):

| Events currently stored | Approximate database size |
| --- | --- |
| A few thousand (light homelab use) | A few MB |
| 10,000 | ~10 MB |
| 100,000 | ~100 MB |
| 1,000,000 | ~1 GB |
| 10,000,000 (very many) | ~10 GB |

Rule of thumb: **~1 KB per stored event**. This is about how many events are currently kept (bounded by the `Retention days` setting), not how many were ever imported - daily/monthly rollups used for historical charts and dashboards stay tiny (a few KB per day) even after old raw events are cleaned up by retention. A busy, public-facing Traefik access log can easily produce tens of thousands of events a day, so size `Retention days` and storage accordingly.

## Compose file

The repository contains the full Compose example at `docker/docker-compose.example.yml`. Copy it to `docker-compose.yml` before starting, or use the examples below. Start minimal and add mounts as you enable plugins.

<details>
<summary>Minimal example</summary>

```yaml
services:
  opensecdash:
    image: konkos1/opensecdash:latest
    container_name: opensecdash
    ports:
      - "8765:8000"
    volumes:
      - opensecdash-data:/data
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    restart: unless-stopped

volumes:
  opensecdash-data:
```

</details>

<details>
<summary>Full example (all plugin log mounts)</summary>

```yaml
services:
  opensecdash:
    image: konkos1/opensecdash:latest
    container_name: opensecdash
    # Lets the CrowdSec plugin reach CrowdSec's Local API on 127.0.0.1 when
    # CrowdSec runs on this same host; see "Plugin file mounts" below.
    network_mode: "host"
    volumes:
      - opensecdash-data:/data
      - /var/log/traefik/access.log:/logs/access.log:ro
      - /var/log/traefik/geoblock.log:/logs/geoblock.log:ro
      - /var/log/crowdsec/crowdsec.log:/logs/crowdsec.log:ro
      - ./assets/assets.json:/assets/assets.json:ro
    # Optional: uncomment the environment block and one or more variables to
    # completely disable plugins at startup. Disabled plugins are hidden from
    # Settings, Diagnostics and navigation, and run no background tasks.
    # environment:
    #   OSD_PLUGIN_CROWDSEC_DISABLED: "true"
    #   OSD_PLUGIN_TRAEFIK_LOG_DISABLED: "true"
    #   OSD_PLUGIN_GEOBLOCK_LOG_DISABLED: "true"
    #   OSD_PLUGIN_GEOIP_DISABLED: "true"
    #   OSD_PLUGIN_JSON_ASSETS_DISABLED: "true"
    #   OSD_PLUGIN_PROXMOX_ASSETS_DISABLED: "true"
    #   OSD_PLUGIN_MQTT_DISABLED: "true"
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    restart: unless-stopped

volumes:
  opensecdash-data:
```

With `network_mode: "host"` the container shares the host's network stack, so the `ports` mapping is dropped and the app is reachable directly on host port `8000` instead of `8765`.

</details>

Start the app:

```bash
docker compose up -d
```

Then open:

```text
http://localhost:8765
```

## Ports

The app listens on port `8000` inside the container. The example maps it to host port `8765` to avoid common homelab conflicts:

```yaml
ports:
  - "8765:8000"
```

The internal app host/port can also be overridden, but this is rarely needed:

```yaml
environment:
  OSD_HOST: 0.0.0.0
  OSD_PORT: "8000"
```

If you change `OSD_PORT`, update the port mapping and health check assumptions accordingly.

## Persistent data

Persistent data is stored in `/data` inside the container. Keep this as a named volume or bind mount so the SQLite database survives container replacement:

```yaml
volumes:
  - opensecdash-data:/data
```

The Docker image defaults to:

```text
DATABASE_URL=sqlite:////data/opensecdash.db
AUTO_MIGRATE=true
LOG_FILE_ENABLED=false
```

You normally do not need to set these values in `docker-compose.yml`.

## Optional environment overrides

| Variable | Default in Docker image | When to change it |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:////data/opensecdash.db` | Use a different database path or backend. SQLite under `/data` is recommended for Docker. |
| `AUTO_MIGRATE` | `true` | Set to `false` only if you want to run Alembic migrations manually before starting the app. |
| `LOG_FILE_ENABLED` | `false` | Enable only if you intentionally want an app-managed file log inside a mounted path. Docker stdout/stderr logging is recommended. |
| `LOG_FILE_PATH` | `logs/opensecdash.log` | File log path used when file logging is enabled. Mount the parent directory if you need persistence. |
| `LOG_LEVEL` | `INFO` | Use `DEBUG` temporarily for troubleshooting; it can create much more output. |
| `OSD_HOST` | `0.0.0.0` | Internal bind address for uvicorn. Usually leave unchanged in Docker. |
| `OSD_PORT` | `8000` | Internal uvicorn port. Usually leave unchanged and only change the host-side port mapping. |
| `OSD_TRUSTED_PROXIES` | loopback + private ranges | Comma-separated IPs/CIDRs of reverse proxies whose `X-Forwarded-*` headers are trusted. Empty disables processing; `*` trusts all. See the [reverse proxy guide](reverse-proxy.md). |

Logging settings are stored in the app database after initial setup. Changing `LOG_FILE_ENABLED`, `LOG_FILE_PATH`, or `LOG_LEVEL` later may not override an already-saved Settings value; use the Settings page for runtime logging changes.

### Disabling plugins

Any plugin can be completely disabled with `OSD_PLUGIN_<PLUGIN>_DISABLED=true` (accepted values `1`/`true`/`yes`/`on`). A disabled plugin is not loaded at all: it is hidden from Settings, Diagnostics and the navigation, and runs no background tasks. Its saved settings stay in the database and reappear when the variable is removed.

`<PLUGIN>` is the plugin's directory name or id, uppercased, with `-` replaced by `_`:

| Plugin | Variable |
| --- | --- |
| CrowdSec | `OSD_PLUGIN_CROWDSEC_DISABLED` |
| Traefik Access Log | `OSD_PLUGIN_TRAEFIK_LOG_DISABLED` |
| GeoBlock Log | `OSD_PLUGIN_GEOBLOCK_LOG_DISABLED` |
| GeoIP enrichment | `OSD_PLUGIN_GEOIP_DISABLED` |
| JSON Assets | `OSD_PLUGIN_JSON_ASSETS_DISABLED` |
| Proxmox Assets | `OSD_PLUGIN_PROXMOX_ASSETS_DISABLED` |
| MQTT export | `OSD_PLUGIN_MQTT_DISABLED` (or `OSD_PLUGIN_MQTT_HASS_DISABLED`) |

## Plugin file mounts

OpenSecDash is easiest to operate when it can read the relevant log files locally. In many homelab setups that means running the OpenSecDash container on the same Docker host or guest as Traefik, GeoBlock, CrowdSec, and similar tools, then mounting their log files read-only into the container.

For persistent data, both a named Docker volume and a host bind mount such as `./data:/data` are supported. On startup, the container fixes `/data` ownership and then runs the app as the unprivileged `opensecdash` user.

If those tools run on a different host/VM, you need to make their logs available to OpenSecDash first, for example with bind mounts, shared storage, or another log shipping approach.

Plugins that read local files need those files mounted into the container; see the full Compose example above for the log and `assets.json` mounts.

These container-side paths (`/logs/access.log`, `/logs/geoblock.log`, `/logs/crowdsec.log`) already match the Traefik, GeoBlock, and CrowdSec plugin defaults, so a fresh install works out of the box once the mounts above are in place. Only change the paths on the Settings page if you mount the logs somewhere else.

`assets.json` is mounted under a dedicated `/assets` path rather than under `/data`: `/data` is owned and recursively chowned to the unprivileged `opensecdash` user on container startup, and a read-only file bind-mounted underneath it can't be chowned. Set the JSON Assets plugin's `Source` setting to `/assets/assets.json` to match this mount.

For CrowdSec ban/unban actions and decision sync, the recommended way is the Local API connection - it needs no extra mounts, just dedicated credentials entered in Settings (plus `network_mode: "host"` when CrowdSec runs on the same host); see [CrowdSec plugin: Connecting via the Local API](../plugins/crowdsec.md#connecting-via-the-local-api-recommended). Running the `cscli` binary inside the container instead is documented as an [alternative](../plugins/crowdsec.md#alternative-cscli-binary-in-docker).

## Docker logging

Docker installs log to stdout/stderr by default and should let Docker rotate logs. Docker log rotation is configured by the Compose `logging` section because it cannot be baked into the image itself:

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

Inspect logs with:

```bash
docker compose logs opensecdash --tail=500
```

The debug ZIP still works when file logging is disabled. In that case, `opensecdash-log.txt` explains how to collect Docker logs.
