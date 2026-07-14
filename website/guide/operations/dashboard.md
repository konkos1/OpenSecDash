# Dashboard

The Dashboard is the live overview of current homelab security activity. It is a responsive widget container rather than a fixed report: OpenSecDash provides cross-plugin widgets, and enabled plugins can contribute their own validated counters and lists.

## What the Dashboard shows

Depending on enabled plugins and available data, the default layout can include:

- external and internal access activity today
- CrowdSec bans and GeoBlock events
- assets and available application updates
- busiest access and attack hours
- top countries and scenarios
- top Insights and recent security events
- a 30-day security-event trend

Cards link to the matching Events, Access, Assets, or Rollups view when a meaningful drill-down exists.

## Customize the layout

Select **Customize dashboard** to choose which widgets are visible and change their order.

1. Clear a widget checkbox to hide it.
2. Use the up/down controls to change its position.
3. Select **Apply layout** to save the result.
4. Use **Reset layout** to return to the current default widget set.

When internal authentication is enabled, every user has an independent layout. Without internal sign-in, the layout is shared by the instance.

If a plugin is disabled, its widgets disappear. Enabling it again adds missing plugin widgets back to the saved layout without discarding the user's existing choices.

## Live updates

Dashboard auto-refresh is enabled by default and reacts to new events without requiring a full page reload. Scroll positions are preserved when a section refreshes.

The setting **Auto-refresh dashboard/CrowdSec/diagnostics/assets pages** controls this behavior. Consider disabling it when OpenSecDash sits behind a reverse proxy whose own access log is imported into OpenSecDash; otherwise the dashboard's refresh requests can create additional access events about the dashboard itself.

Events and Access use their own separate Live/Snapshot control and are not affected by this setting.

## Fast first paint

The dashboard frame and widget placeholders render before expensive widget queries complete. Each result block is then filled with server-rendered HTML. This keeps navigation responsive on larger SQLite databases without introducing a separate frontend application or caching potentially stale security data in the browser.

## Today versus history

The Dashboard intentionally focuses on current activity. Small delta badges compare supported counters with yesterday's stored rollup.

Use the [Rollup Explorer](./dashboard-rollups.md) for a specific historical day or month. Retention can remove old raw events without removing their compact daily and monthly summaries.

