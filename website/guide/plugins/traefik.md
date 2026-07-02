# Traefik Access Log Plugin

The Traefik Access Log plugin imports and classifies reverse proxy access log entries.

It powers the Access page and contributes access events to the Events page, Dashboard, IP Explorer, GeoIP enrichment, and dashboard rollups.

## Typical setup

In Docker, mount the Traefik access log read-only into the OpenSecDash container and configure the same path in Settings.

```yaml
volumes:
  - /var/log/traefik/access.log:/logs/access.log:ro
```

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables Traefik access log import. |
| Traefik access log path | Path to the Traefik JSON `access.log`. In Docker, mount the host log read-only into the container. |
| Poll interval seconds | How often the log file is checked for appended lines and rotation. |
| Hide local IPs by default | Opens the Access page with local/private IPs hidden unless the filter is changed manually. |

## Local IP filtering

The Access page has two runtime filters:

- **Hide local IPs**: show only public/non-local clients.
- **Show local IPs**: show only local/private clients.

The plugin setting **Hide local IPs by default** only controls the initial Access page view. Users can still change the filter directly on the Access page.

## Log format

The plugin expects Traefik JSON access logs. It reads fields such as:

- `ClientHost`
- `RequestHost`
- `RequestMethod`
- `RequestPath`
- `DownstreamStatus` / `OriginStatus`
- `RouterName`
- `ServiceName`
- `request_User-Agent`

Status codes are classified into OpenSecDash event types such as `access.allowed`, `access.denied`, and `access.error`.
