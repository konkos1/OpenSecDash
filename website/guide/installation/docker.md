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

## Compose file

Two `docker-compose.yml` examples: a minimal one that just gets the app running, and a full one with all plugin log mounts and `cscli` wired in for CrowdSec actions. Start minimal and add mounts as you enable plugins.

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
<summary>Full example (all plugin log mounts + cscli)</summary>

```yaml
services:
  opensecdash:
    image: konkos1/opensecdash:latest
    container_name: opensecdash
    # cscli needs to reach CrowdSec's Local API on the host; see "Plugin file mounts" below.
    network_mode: "host"
    volumes:
      - opensecdash-data:/data
      - /var/log/traefik/access.log:/logs/access.log:ro
      - /var/log/traefik/geoblock.log:/logs/geoblock.log:ro
      - /var/log/crowdsec/crowdsec.log:/logs/crowdsec.log:ro
      - /usr/bin/cscli:/usr/local/bin/cscli:ro
      - /etc/crowdsec:/etc/crowdsec:ro
      - ./assets/assets.json:/assets/assets.json:ro
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

Or:

```bash
cp docker-compose.example.yml docker-compose.yml
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

Logging settings are stored in the app database after initial setup. Changing `LOG_FILE_ENABLED`, `LOG_FILE_PATH`, or `LOG_LEVEL` later may not override an already-saved Settings value; use the Settings page for runtime logging changes.

## Plugin file mounts

OpenSecDash is easiest to operate when it can read the relevant log files locally. In many homelab setups that means running the OpenSecDash container on the same Docker host or guest as Traefik, GeoBlock, CrowdSec, and similar tools, then mounting their log files read-only into the container.

For persistent data, both a named Docker volume and a host bind mount such as `./data:/data` are supported. On startup, the container fixes `/data` ownership and then runs the app as the unprivileged `opensecdash` user.

If those tools run on a different host/VM, you need to make their logs available to OpenSecDash first, for example with bind mounts, shared storage, or another log shipping approach.

Plugins that read local files need those files mounted into the container; see the full Compose example above for the log and `assets.json` mounts.

These container-side paths (`/logs/access.log`, `/logs/geoblock.log`, `/logs/crowdsec.log`) already match the Traefik, GeoBlock, and CrowdSec plugin defaults, so a fresh install works out of the box once the mounts above are in place. Only change the paths on the Settings page if you mount the logs somewhere else.

`assets.json` is mounted under a dedicated `/assets` path rather than under `/data`: `/data` is owned and recursively chowned to the unprivileged `opensecdash` user on container startup, and a read-only file bind-mounted underneath it can't be chowned. Set the JSON Assets plugin's `Source` setting to `/assets/assets.json` to match this mount.

The CrowdSec plugin additionally shells out to `cscli` for ban/unban actions and decision sync, which needs more than a log mount; see [CrowdSec plugin: cscli in Docker](../plugins/crowdsec.md#cscli-in-docker).

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
