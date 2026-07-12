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
| Log timestamp timezone | Assumed timezone for log lines that don't include a timezone offset themselves. Defaults to `UTC`. See [Storage is always UTC](#storage-is-always-utc) below. |
| Auto-refresh dashboard/CrowdSec/diagnostics/assets pages | Keeps those four pages updating in the background (scroll position is preserved) without a manual reload. Defaults to enabled. Disable it if OpenSecDash's own web UI is itself behind a reverse proxy whose access log OpenSecDash imports, since the periodic refresh requests would otherwise show up as `access.*` events about OpenSecDash itself. The Events/Access page has its own separate Live/Snapshot toggle and is unaffected by this setting. |
| Check for OpenSecDash updates | Periodically asks GitHub for the latest OpenSecDash release (one small API request every few hours; no data about your instance is sent). When a newer version exists, a clearly visible hint with the version appears in the footer, linking to the release. Defaults to enabled. |

## Notifications

The **Notifications (Email)** section configures SMTP email alerts, including the
master switch, the OpenSecDash base URL for email links, SMTP transport settings,
sender, and recipient. The SMTP password is encrypted at rest like other sensitive
settings. See [Notifications](./notifications.md) for setup, default rules, and
delivery behavior.

## Storage is always UTC

Every timestamp OpenSecDash stores is normalized to UTC. The `Timezone` setting only controls how timestamps are *displayed*; it never changes what's stored. This means changing the timezone setting later never leaves old data inconsistent with new data - everything in the database was already UTC, and only the display layer re-reads the current setting.

Most log formats OpenSecDash reads already state their own timezone explicitly (for example Traefik's `StartUTC` field, or CrowdSec's `time="...+02:00"` entries), so those are converted to UTC unambiguously and are unaffected by any setting.

Some log formats don't include a timezone offset at all - for example the GeoBlock Traefik plugin logs plain timestamps like `2026/06/20 04:00:54` with no offset, which is really the log-writer's local wall-clock time. For those, OpenSecDash needs to be told which timezone that naive timestamp is actually in before it can convert it to UTC - that's what `Log timestamp timezone` is for. It defaults to `UTC`, which is correct if the host/container producing that log already runs its system clock in UTC (the common case for Docker deployments). Set it to the actual local timezone of that log source (for example `Europe/Berlin`) if it doesn't.

## Secrets are encrypted at rest

Sensitive settings values - anything whose key marks it as a password, token, secret, API key, or credential (for example the GitHub API token, the Proxmox token secret, or the MQTT password) - are stored encrypted in the database, not in plaintext. This happens automatically; there is nothing to configure. Existing plaintext values from older versions are encrypted once on the next start.

By default the encryption key is generated on first use and stored as `opensecdash.secret` next to the database file (in Docker: inside the `/data` volume, file mode `600`). That protects a leaked or carelessly shared *database file* - but not a backup of the whole `/data` volume, since the key sits next to the data it protects.

For stricter setups, provide the key yourself via the `OSD_SECRET_KEY` environment variable so it never touches the data volume or its backups:

```yaml
services:
  opensecdash:
    environment:
      # generate once with:  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
      OSD_SECRET_KEY: "<your-generated-key>"
```

Switching from the auto-generated key file to `OSD_SECRET_KEY` later is seamless: as long as the old `opensecdash.secret` file is still present, it keeps working as a decrypt-only fallback, and on the next start every stored secret is automatically re-encrypted under the new key (the log confirms it). After that start, the old key file can be deleted. The same mechanism covers deliberate key rotation - set the new key in `OSD_SECRET_KEY` and provide the previous one as `opensecdash.secret` next to the database for one start.

Two honest limitations to be aware of:

- Encryption at rest does not protect against an attacker who can already run code in the container or on the host - they can read the key the same way OpenSecDash does.
- If a key is lost entirely (file deleted and no fallback available), the stored secrets cannot be recovered. OpenSecDash keeps working; the affected values simply read as unset and need to be re-entered in Settings, which re-encrypts them under the current key.

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
