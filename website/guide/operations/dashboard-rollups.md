# Rollups

Rollups are precomputed counters for historical event activity. They keep long-term summaries available even after raw events are removed by retention.

The rollup page is available only when at least one event datasource plugin is enabled, such as CrowdSec, GeoBlock Log, or Traefik Access Log.

## Daily and monthly rollups

OpenSecDash keeps two rollup levels:

- **Daily rollups** for calendar months that overlap the Dashboard's rolling 30-day trend.
- **Monthly rollups** for older completed calendar months.

OpenSecDash checks rollup compaction regularly in a background task based on the current system date/time. A completed month remains available at daily precision while any of its days can appear in the Dashboard's 30-day trend. Once the whole month is outside that window, OpenSecDash aggregates its daily rows into one compact monthly rollup and removes the daily rows. Monthly rollups are kept long-term because they are very small.

Raw events and access events are still managed by normal retention. Before retention deletes raw events, OpenSecDash keeps the required daily/monthly rollups. Recent daily precision therefore remains available even when raw-event retention is shorter than 30 days. This keeps the database small while preserving the Dashboard trend and historical activity summaries.

## Rollup Explorer

Use **Rollups** in the navigation to open the Rollup Explorer.

You can select:

- a specific day, backed by daily rollups
- a specific calendar month, backed by daily rollups while the month overlaps the Dashboard trend and by monthly rollups after compaction

The page shows summary cards and breakdown tables.

## Summary metrics

| Metric | Meaning |
| --- | --- |
| External access | Access events with a public/routable client IP. |
| Internal access | Access events with a local/private/reserved client IP. Access events without an IP are ignored for this split and remain visible in the Event Explorer. |
| Security events | Events whose type starts with `security.`. |
| Bans | Events whose type starts with `security.ban`. |
| Geoblocks | Events with type `security.geoblock`. |
| Total events | All rolled-up events, including access events without an IP. |

## Detail metrics

| Metric | Meaning |
| --- | --- |
| Event type | Counts grouped by OpenSecDash event type. |
| Scenario | Counts grouped by correlated scenario value, such as CrowdSec scenarios. |
| Country | Counts grouped by event country. |

## Dashboard

The Dashboard intentionally focuses on today's activity plus a compact 30-day security-event trend. Use the Rollup Explorer for detailed historical summaries.
