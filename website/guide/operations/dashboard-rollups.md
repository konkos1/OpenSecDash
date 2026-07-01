# Dashboard rollups

Dashboard rollups are precomputed daily counters used for fast historical dashboard summaries.

The live dashboard focuses on **today**. Rollups are different: they represent the latest completed historical day with rollup data, not the current day.

## What is shown

When at least one rollup-capable event datasource is enabled, the Dashboard can show a **Historical rollup events** section with:

- total rolled-up events for the selected historical day
- top event types, for example `access.error` or `security.ban`
- top scenarios, for example CrowdSec scenario names when available in event payloads

Rollup-capable event datasources currently include:

- CrowdSec
- GeoBlock Log
- Traefik Access Log

If none of these plugins are enabled, the rollup widget is hidden.

## Which day is used

OpenSecDash intentionally does **not** use today for historical rollups.

Instead it selects the newest rollup day that is earlier than today:

```text
latest aggregation day < today
```

This avoids showing a partial current-day rollup next to live dashboard widgets.

Example:

```text
Today:      2026-07-01
Rollup day: 2026-06-30
```

If no completed historical rollup exists yet, the widget shows no data.

## Metrics

Rollup rows are stored by metric name:

| Metric | Meaning |
| --- | --- |
| `event_type` | Counts grouped by OpenSecDash event type. |
| `scenario` | Counts grouped by correlated scenario value, such as CrowdSec scenarios. |

## Live data vs rollup data

| Dashboard area | Data source |
| --- | --- |
| Today's cards | Live/raw events from today. |
| Top countries today | Live/raw events from today. |
| Top attack/access hours today | Live/raw events from today. |
| Historical rollup events | Precomputed completed-day rollups. |

This split keeps the dashboard useful for live monitoring while still allowing fast summaries of historical data.
