# CrowdSec Plugin

The CrowdSec plugin imports CrowdSec ban history and can execute ban/unban actions through `cscli`.

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables CrowdSec log import and CrowdSec actions. |
| CrowdSec log path | Path to `crowdsec.log`. In Docker, mount the host log read-only into the container. Ban history, scenarios, and countries are derived from matching log lines. |
| cscli path | Command or absolute path used for active decisions and ban/unban actions. |
| CrowdSec poll interval seconds | How often the CrowdSec log is checked for appended ban history entries. |

OpenSecDash also synchronizes active CrowdSec decisions for the Unban workflow.

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
