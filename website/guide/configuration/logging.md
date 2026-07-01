# Logging

OpenSecDash writes service logs to stdout/stderr. Optional file logging can be enabled in Settings.

## Docker

For Docker installs, file logging is disabled by default in the image.

Docker log rotation should be handled by the container runtime. The example compose file uses:

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

Collect logs with:

```bash
docker compose logs opensecdash --tail=500
```

## Bare metal

For bare-metal installs, file logging can be useful if you want logs under `/var/log/opensecdash/`. If enabled, configure log rotation with your operating system.
