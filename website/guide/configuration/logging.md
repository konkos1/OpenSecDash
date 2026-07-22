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

For bare-metal installs, file logging can be useful if you want logs under `/var/log/opensecdash/`. If enabled, configure log rotation with your operating system. OpenSecDash does not rotate this file itself; enabling file logging without logrotate or an equivalent external policy allows it to grow without a bound.

Unhandled request errors are written to the service log with a traceback and only the HTTP method and path as request context. Query strings, form data, cookies, and headers are not included. The response shown to the client remains generic.
