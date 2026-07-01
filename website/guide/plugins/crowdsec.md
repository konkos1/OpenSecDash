# CrowdSec Plugin

The CrowdSec plugin imports CrowdSec ban history and can execute ban/unban actions through `cscli`.

## Settings

- Enable the plugin
- CrowdSec log path
- `cscli` path
- Poll interval for log imports

## Typical setup

In Docker, mount the CrowdSec log read-only into the OpenSecDash container and configure the plugin path in Settings.

```yaml
volumes:
  - /var/log/crowdsec.log:/var/log/crowdsec.log:ro
```

## Actions and dry run

OpenSecDash has an action simulation mode. While dry run is enabled, ban/unban actions are recorded but not executed.

When dry run is disabled, unban buttons are shown only when OpenSecDash knows about an active CrowdSec ban decision. Decisions are synchronized from `cscli decisions list -o json`.

## cscli diagnostics

The CrowdSec page and IP Explorer show `cscli` status. In dry-run mode, `cscli` errors are not shown as prominent action errors because real actions are not executed.
