# Settings

OpenSecDash settings are available from the Settings page. Dependent settings usually stay visible but are greyed out until their parent setting or plugin is enabled, so you can still see what can be configured.

## General settings

| Setting | What it does |
| --- | --- |
| Primary Domain | Shown as the identity of this OpenSecDash instance. Useful when screenshots/debug reports need to identify which homelab instance they came from or simply as branding. |
| Language | UI language. Technical event identifiers such as `access.denied` stay unchanged. |
| Retention days | How long raw events are kept in the local database. Rollups are preserved before cleanup so historical summaries remain available. |
| Default Events mode | Starts the Events- and Access page in Live or Snapshot mode. Live keeps updating; Snapshot freezes the current view for investigation. |
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
| Search | Searches visible/understandable system and app fields such as hostname, VMID, system type, app name, host URL, versions, release URL, active/inactive, and update state. Internal source IDs are not searched. |
| Source | Filters by asset source, for example JSON Assets or Proxmox Assets. |
| Show inactive | Includes inactive systems/apps that were previously imported but no longer seen by their source. |
| Updates only | Shows only systems that currently have apps with available updates. |
| Proxmox sync | Manually synchronizes Proxmox Assets when that plugin is enabled. |
| JSON sync | Manually synchronizes JSON Assets when that plugin is enabled. |
| Check updates | Checks GitHub release URLs for assets. During one run, the same repository is queried only once. |
| Publish MQTT now | Publishes currently publishable app update states when MQTT is enabled. |

Systems can be marked **stale** when they have not been seen recently. Proxmox Assets use a shorter threshold than JSON/manual sources because Proxmox syncs more frequently.

On a system detail page, OpenSecDash shows a host tab row directly below the apps table. The combined view is the default; selecting a host filters both insights and events to that app host.

Event tables on this page are intentionally capped for performance: the combined view shows the newest 100 events, and each host-specific tab shows the newest 50 events for that host.

## UI row limits

Some tables/lists intentionally show only the newest or top rows for performance and readability:

| Area | Limit |
| --- | --- |
| Events page | Newest 200 matching events |
| Access page | Newest 200 matching access events |
| CrowdSec history | Newest 100 ban history entries |
| IP Explorer insights | Up to 50 latest insight types |
| IP Explorer events | Newest 200 events for the IP |
| Asset system combined events | Newest 100 events |
| Asset host tab events | Newest 50 events for that host |
| Diagnostics recent actions | Newest 20 manual actions |
| Dashboard top lists | Top 5 rows |
| Dashboard latest security events | Newest 10 security events |

Plugin-specific settings are documented on the individual [plugin pages](../plugins/index.md).
