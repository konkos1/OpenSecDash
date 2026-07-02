# Rollups

Rollups are precomputed counters for historical event activity. They keep long-term summaries available even after raw events are removed by retention.

The rollup page is available only when at least one event datasource plugin is enabled, such as CrowdSec, GeoBlock Log, or Traefik Access Log.

## Daily and monthly rollups

OpenSecDash keeps two rollup levels:

- **Daily rollups** for the current calendar month.
- **Monthly rollups** for completed calendar months.

OpenSecDash checks rollup compaction regularly in a background task based on the current system date/time. When a month is complete, it aggregates that month's daily rows into one compact monthly rollup and then removes the daily rows for that month. The previous day's daily rollup is kept for one extra day so Dashboard comparison badges still work on the first day of a new month. Monthly rollups are kept long-term because they are very small.

Raw events and access events are still managed by normal retention. Before retention deletes raw events, OpenSecDash keeps the required daily/monthly rollups: completed months are compacted first, and daily rollups for the current month are not deleted by retention. This keeps the database small while preserving historical activity summaries.

## Rollup Explorer

Use **Rollups** in the navigation to open the Rollup Explorer.

You can select:

- a specific day, backed by daily rollups
- a specific calendar month, backed by monthly rollups for completed months or daily rollups for the current month

The page shows summary cards and breakdown tables.

## Summary metrics

| Metric | Meaning |
| --- | --- |
| Total events | All rolled-up events. |
| Access events | Events whose type starts with `access.`. |
| Security events | Events whose type starts with `security.`. |
| Bans | Events whose type starts with `security.ban`. |
| Geoblocks | Events with type `security.geoblock`. |

## Detail metrics

| Metric | Meaning |
| --- | --- |
| Event type | Counts grouped by OpenSecDash event type. |
| Scenario | Counts grouped by correlated scenario value, such as CrowdSec scenarios. |
| Country | Counts grouped by event country. |

## Dashboard

The Dashboard intentionally focuses on today's activity. Historical rollups live in the Rollup Explorer instead of Dashboard widgets.
