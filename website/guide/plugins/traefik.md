# Traefik Access Log Plugin

The Traefik Access Log plugin imports and classifies reverse proxy access log entries.

It powers the Access page and contributes access events to the Events page and IP Explorer.

## Typical setup

In Docker, mount the Traefik access log read-only into the OpenSecDash container and configure the plugin path in Settings.

```yaml
volumes:
  - /var/log/traefik/access.log:/logs/traefik.log:ro
```
