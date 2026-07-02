# Docker Compose installation

Docker Compose is the recommended installation method.

docker-compose.yml example:

```yml
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

Plugins that read local files need those files mounted into the container. Examples:

```yaml
volumes:
  - opensecdash-data:/data
  - /var/log/traefik/access.log:/logs/access.log:ro
  - /var/log/traefik/geoblock.log:/logs/geoblock.log:ro
  - /var/log/crowdsec.log:/logs/crowdsec.log:ro
  - ./assets.json:/data/assets.json:ro
```

Then configure the same container paths on the Settings page.

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
