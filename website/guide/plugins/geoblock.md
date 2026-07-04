# GeoBlock Log Plugin

The GeoBlock Log plugin imports denied requests from the Traefik GeoBlock plugin log and stores them as security events.

It is intended for setups using the Traefik plugin from `github.com/PascalMinder/geoblock` or a compatible log format.

## What it creates

Matching log lines are imported as:

```text
security.geoblock
```

The plugin extracts, when available:

- event time
- source IP
- country
- original raw log line

These events appear on the Dashboard, Events page, IP Explorer, and can be enriched by GeoIP when GeoIP is enabled.

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables GeoBlock log watching/import. |
| GeoBlock log path | Path to `geoblock.log`. In Docker, mount the host log read-only into the container. |
| Poll interval seconds | How often the log file is checked for new entries. |

## Typical setup

In Docker, mount the GeoBlock log read-only into the OpenSecDash container and configure the same path in Settings.

```yaml
volumes:
  - /var/log/traefik/geoblock.log:/logs/geoblock.log:ro
```

The plugin's `GeoBlock log path` already defaults to `/logs/geoblock.log`, so no Settings change is needed if you mount it there.

## Diagnostics

Diagnostics shows whether the configured log file exists and is readable. If the path is wrong or the file is not mounted into the container, the plugin reports an error.
