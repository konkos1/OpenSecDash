# Settings

OpenSecDash settings are available from the Settings page. Plugin-specific settings are shown only when the corresponding plugin is enabled.

## General

| Setting | What it does |
| --- | --- |
| Primary Domain | Shown as the identity of this OpenSecDash instance |
| Language | UI language; technical event identifiers stay unchanged |
| Retention days | How long raw events should be kept, depending on cleanup configuration |
| Default Events mode | Start the Events page in Live or Snapshot mode |
| Theme | Dark, light, or automatic browser/system theme |
| Timezone | Display timestamps in `auto`, `UTC`, or an IANA timezone such as `Europe/Berlin` |

## Actions

| Setting | What it does |
| --- | --- |
| Action simulation | Dry-run mode records actions without executing them |
| Execute via plugin | Allows configured action plugins to execute real actions |

Dry-run is the safer default. Only disable it when you trust and understand the configured action plugins.

## Logging

| Setting | What it does |
| --- | --- |
| Write log file | Enables an additional file log |
| Log file path | Path for the optional log file |
| Log level | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |

Service/journal logging remains available even when file logging is disabled.
