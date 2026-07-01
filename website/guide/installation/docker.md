# Docker Compose installation

Docker Compose is the recommended installation method.

```bash
cp docker-compose.example.yml docker-compose.yml
docker compose up -d
```

Then open:

```text
http://localhost:8765
```

## Ports

The app listens on port `8000` inside the container. The example maps it to host port `8765`:

```yaml
ports:
  - "8765:8000"
```

## Persistent data

Persistent data is stored in `/data` inside the container. The default database URL is:

```text
sqlite:////data/opensecdash.db
```

## Docker logging

Docker installs should log to stdout/stderr and let Docker rotate logs. The example compose file sets:

```yaml
environment:
  LOG_FILE_ENABLED: "false"
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
