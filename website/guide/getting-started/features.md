# Features

OpenSecDash follows a simple flow:

```text
Datasources → Enrichment → Event Store → Correlation → Dashboard / Explorer / Actions
```

![OpenSecDash event pipeline](/assets/readme/event-pipeline.svg)

## Live-first dashboard

The dashboard gives you a quick overview of current homelab security activity:

- CrowdSec bans
- GeoBlock events
- external and internal access events
- assets and available updates
- top countries
- busiest attack/access hours
- recent security context
- small comparison badges based on yesterday's rollups when available

Dashboard content comes from a widget container. Enabled plugins can contribute
validated counter, table, feed, and trend widgets; the core renders those descriptors
and plugins do not provide dashboard HTML. For example, CrowdSec contributes its
active-ban counter and GeoBlock contributes today's geoblock counter, while cross-plugin
lists such as top countries remain core-owned.

To adapt the dashboard for a homelab, open **Customize dashboard**. Clear a widget's
checkbox to hide it, use the up/down controls to change its order, and choose **Apply
layout**. **Reset layout** removes the saved customization and restores the default
ordering and visibility. A widget from a disabled plugin disappears after reload; if
the plugin is enabled again later, its widget is added back visibly. Widget links open
the corresponding filtered Events or Assets view. With internal authentication
enabled, each user has an independent dashboard layout; without it, the layout remains
global to the instance.

The Events page supports **Live** and **Snapshot** modes. Live mode keeps the UI fresh. Snapshot mode freezes the current view so you can filter and inspect without the table moving under your mouse.

Historical rollups live in the [Rollup Explorer](../operations/dashboard-rollups.md), while the Dashboard stays focused on today's activity.

## Events and access logs

OpenSecDash stores events with structured fields and optional raw data:

- event type, plugin, source
- IP address, country, city, ASN, ISP
- hostname, path, method, status code
- severity
- event timestamp
- plugin-specific JSON payload

Events use a stable taxonomy such as:

```text
access.allowed
access.denied
access.error
security.ban
security.geoblock
security.torblock
action.executed
action.failed
```

## Search and filters

The Events and Access views support practical filters for homelab investigations:

- event type, including wildcards such as `access.*`
- IP address
- country code, country exclusions, and comma-separated country lists
- exact status code or an inclusive status-code range
- ASN, hostname, and asset
- path
- plugin/source
- local-IP include/exclude behavior
- text search with boolean expressions such as `wp-login && (404 || 403)`

Operator filters are structured URL parameters rather than a free-text query language. For example, `/events?country_in=RU,CN` is the structured equivalent of `country IN (RU,CN)` and investigates traffic from a country list; `/events?country_not=DE` excludes German traffic, and `/access?status_min=400&status_max=499` shows client-error responses. The Events filter form also exposes status ranges, ASN, and hostname; URL filters make the same investigation links shareable.

Choose **Last hour**, **24 hours**, **7 days**, or **30 days** in the time-range picker. The selected range is retained when moving between Events and Access. For a custom range, use a shareable URL such as `/events?range=custom&from=2026-07-12T00:00:00Z&to=2026-07-13T00:00:00Z`.

The header has one global search box. An IP address or CIDR opens the IP Explorer, a matching asset name or hostname opens Asset Explorer, and other searches open matching Events. For example, searching `/wp-login.php` opens Events with that search term.

### Saved views

Both Events and Access have a **Saved views** area above their filters. Give the current filter set a name to save it as a reusable link; a view called **404 errors** can preserve `status_min=400` and `status_max=499` for a reverse-proxy investigation. With internal authentication enabled, saved views belong to the signed-in user, and saving the same name again in the same area replaces that user's view. Without internal authentication, saved views remain global to the instance. Plugins can also show read-only provided views alongside your own views.

Long values such as paths, URLs, user agents, and ISP names are truncated in tables and can be opened in an overlay.

## IP Explorer

The IP Explorer is the “what happened with this address?” view. It combines:

- all events for the IP
- access attempts
- bans/geoblocks
- insights
- manual CrowdSec actions when enabled

For local/private IPs, destructive actions such as bans are intentionally disabled.

## Responsive UI

OpenSecDash is designed to work on phones, tablets, and desktops:

- dashboard cards adapt to the screen
- dense tables become label/value cards on mobile
- long values open in touch-friendly overlays
- Events/Access columns can be customized
- actions are sized for touch input
- navigation stays easy to reach

## Install from your browser

OpenSecDash includes a web app manifest, so modern browsers can add it as an app-like shortcut. This does not require an app store.

For the best install experience, serve OpenSecDash through HTTPS via your reverse proxy.

## Insights engine and correlation

The Insights engine is one of OpenSecDash's core features. It turns noisy homelab events into small, actionable hints that explain why something is interesting.

OpenSecDash creates insights from event patterns, for example:

- possible WordPress/phpMyAdmin/config-file probes from declarative insight rules
- access errors followed by security bans
- geoblocked requests
- manually triggered security bans

Heuristic web-probe rules can be updated from the OpenSecDash website without an app release, while still using declarative JSON data instead of remote code execution. See [Insights engine](../operations/insight-rules.md).

The goal is not to be a SIEM. The goal is to surface useful context quickly and separate meaningful signals from background noise.

## Asset Explorer

The Asset Explorer helps answer:

- Which apps are installed where?
- Which apps have known newer GitHub releases?
- Which systems have apps with updates?
- Which access events are related to a known asset host?

Assets represent services or apps you consciously run, such as Home Assistant, Nextcloud, Vaultwarden, Immich, Jellyfin, Traefik, Grafana, or Uptime Kuma.

## GitHub release checks

For assets with GitHub release URLs, OpenSecDash can check the latest release and mark apps with available updates.

The update checker uses a GitHub token when configured, which is recommended to avoid rate limits. During one update-check run, the same GitHub repository is only queried once even if multiple assets point to it.

## Plugin diagnostics

The Diagnostics page shows:

- registered plugins
- whether each plugin is enabled
- datasource status
- runtime diagnostics
- recent manual actions

This makes it easier to understand whether a missing event is a configuration issue, a disabled plugin, or a runtime error.

## Notifications

The Notifications page can turn matching events and insights into SMTP email
alerts. It keeps delivery history, supports a test email, and uses cooldowns
and digest aggregation to avoid alert floods. See [Notifications](../configuration/notifications.md)
for setup and the built-in rules.
