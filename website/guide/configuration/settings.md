# Settings

OpenSecDash settings are available from the Settings page. Plugin-specific settings are shown only when the corresponding plugin is enabled.

## General settings

| Setting | What it does |
| --- | --- |
| Primary Domain | Shown as the identity of this OpenSecDash instance. Useful when screenshots/debug reports need to identify which homelab instance they came from. |
| Language | UI language. Technical event identifiers such as `access.denied` stay unchanged. |
| Retention days | How long raw events should be kept, depending on cleanup/retention behavior. |
| Default Events mode | Starts the Events page in Live or Snapshot mode. Live keeps updating; Snapshot freezes the current view for investigation. |
| Theme | Dark, light, or automatic browser/system theme. |
| Timezone | Display timestamps in `auto`, `UTC`, or an IANA timezone such as `Europe/Berlin`. |

## Actions

| Setting | What it does |
| --- | --- |
| Action simulation | Dry-run mode records actions without executing them. This is the safer default. |
| Execute via plugin | Allows configured action plugins to execute real actions. Disable dry-run only when plugins and permissions are trusted. |

When dry-run is enabled, action buttons may still be visible so you can test workflows without changing external systems.

## Logging

| Setting | What it does |
| --- | --- |
| Write log file | Enables an additional app log file. In Docker installs this should usually stay disabled. |
| Log file path | Path for the optional file log, for example `logs/opensecdash.log` or `/var/log/opensecdash/opensecdash.log`. |
| Log level | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. Use `DEBUG` only temporarily because logs grow faster. |

Service/journal logging remains available even when file logging is disabled. Docker installs should normally use stdout/stderr and Docker log rotation.

## Events and Access columns

Events and Access tables support configurable visible columns. Use the column selector (cog-symbol) on each page to choose what is shown. The setting is saved separately for Events and Access.

Available columns:

| Column | Description |
| --- | --- |
| Time | Event timestamp in the configured display timezone. |
| Type | Event type, for example `access.allowed` or `security.ban`. |
| Severity | Event severity. |
| IP | Source/client IP, linked to the IP Explorer. |
| Country | GeoIP country code/name when available. |
| City | GeoIP city when available. |
| HTTP status | HTTP status code with expandable label. |
| Path | Request path. Long values are truncated and can be opened in an overlay. |
| URL | Reconstructed URL when host/path data is available. |
| Host | Request host or matched asset host. |
| Method | HTTP method. |
| User agent | User agent string. Long values can be opened in an overlay. |
| Router | Router name from supported reverse proxy logs. |
| Service | Service/backend name from supported reverse proxy logs. |
| ASN | GeoIP ASN, for example `AS15169`. |
| ISP | GeoIP ISP/organization. Long values can be opened in an overlay. |

Default Events columns:

```text
time, type, severity, ip, country, status, url
```

Default Access columns:

```text
time, ip, host, method, status, path
```

## Events and Access search

The Events and Access pages have a text search field for quick investigations. The search checks common event fields such as event type, plugin/source, severity, IP, country, city, ASN, ISP, hostname, method, status code, path, timestamps, plugin JSON payload, and raw event data.

Simple search:

```text
wp-login
```

This finds events containing `wp-login` in any searchable field.

Use `&&` for AND:

```text
wp-login && 404
```

This finds events that contain both `wp-login` and `404`.

Use `||` for OR:

```text
404 || 403
```

This finds events containing either `404` or `403`.

Use parentheses to group expressions:

```text
wp-login && (404 || 403)
```

This finds events containing `wp-login` and either `404` or `403`.

Use quotes when a search term contains spaces:

```text
"Mozilla/5.0"
```

Notes:

- `&&` has higher precedence than `||`.
- Parentheses can be used to make the intended logic explicit.
- A plain search without `&&`, `||`, or parentheses is treated as one substring search.
- The special value `-` can be used for country searches to find events without a country value.

## Asset Explorer filters

The Asset Explorer has several user-facing controls:

| Control | What it does |
| --- | --- |
| Search | Searches visible/understandable system and app fields such as hostname, VMID, system type, app name, host URL, versions, release URL, update type, active/inactive, and update state. Internal source IDs are not searched. |
| Source | Filters by asset source, for example JSON Assets or Proxmox Assets. |
| Show inactive | Includes inactive systems/apps that were previously imported but no longer seen by their source. |
| Updates only | Shows only systems that currently have apps with available updates. |
| Proxmox sync | Manually synchronizes Proxmox Assets when that plugin is enabled. |
| Import | Manually imports JSON Assets when that plugin is enabled. |
| Check updates | Checks GitHub release URLs for assets. During one run, the same repository is queried only once. |
| Publish MQTT now | Publishes currently publishable app update states when MQTT is enabled. |

Systems can be marked **stale** when they have not been seen recently. Proxmox Assets use a shorter threshold than JSON/manual sources because Proxmox syncs more frequently.

## JSON Assets plugin settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables the JSON Assets plugin and asset navigation/widgets. |
| Source type | Loads `assets.json` from a local file or URL. |
| Source | Path or URL to `assets.json`. Missing apps are marked inactive and kept for history. |
| Master for app values | Controls whether version/release URL are maintained in OpenSecDash or overwritten from `assets.json` for existing apps. |
| Inventory interval seconds | How often `assets.json` is reloaded automatically. Use `0` to disable automatic reloads. |
| GitHub API token | Optional token for release checks to avoid GitHub rate limits. |
| GitHub release check interval seconds | How often GitHub releases are checked automatically. Use `0` to disable automatic checks. |

## Proxmox Assets plugin settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables Proxmox node/guest import and optional app declarations from Proxmox notes. |
| Proxmox API URL | Base URL, for example `https://pve.example.local:8006`. |
| API token ID | Token ID, for example `opensecdash@pve!inventory`. |
| API token secret | Secret value for the API token. |
| Verify TLS certificate | Enables certificate verification. Disable only for trusted self-signed homelab certificates. |
| Poll interval seconds | How often Proxmox assets are synchronized. Default: `300`. |

See [Proxmox Assets](../plugins/proxmox-assets.md) for token permissions and notes metadata.

## CrowdSec plugin settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables CrowdSec log import and CrowdSec actions. |
| CrowdSec log path | Path to `crowdsec.log`. Ban history, scenarios, and countries are derived from matching log lines. |
| cscli path | Command or absolute path used for active decisions and ban/unban actions. |
| CrowdSec poll interval seconds | How often the CrowdSec log is checked for appended ban history entries. |

OpenSecDash also synchronizes active CrowdSec decisions for the Unban workflow.

## GeoIP plugin settings

| Setting | What it does |
| --- | --- |
| Enabled | Adds country, city, ASN, and ISP to new public-IP events when the producer did not already provide them. |
| Provider | GeoIP provider. The bundled provider is `ip-api.com`. |
| Cache TTL days | How long successful lookups stay cached before being refreshed. |
| Timeout seconds | HTTP timeout for one GeoIP provider request. |

Private/local/reserved IPs are skipped.

## Traefik Access Log plugin settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables Traefik access log import. |
| Log path | Path to the Traefik access log file. In Docker, mount the host log read-only into the container. |
| Poll interval seconds | How often the log file is checked for appended lines. |
| Hide local IPs by default | Controls whether local/private IPs are hidden by default in access views. |

## GeoBlock Log plugin settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables GeoBlock log import. |
| Log path | Path to the GeoBlock log file. |
| Poll interval seconds | How often the log file is checked for appended lines. |

## MQTT / Home Assistant plugin settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables publishing app update information to MQTT/Home Assistant. |
| Host | MQTT broker hostname or IP. |
| Port | MQTT broker port, usually `1883`. |
| Username | Optional MQTT username. |
| Password | Optional MQTT password. |
| Topic prefix | Prefix for MQTT topics, for example `opensecdash`. |
| Publish interval seconds | How often publishable app update information is sent automatically. |

MQTT buttons in the Asset Explorer are available for assets from any asset source when MQTT is enabled and the asset has enough version/release metadata.
