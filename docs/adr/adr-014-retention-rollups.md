# ADR-014: Retention & Rollups

> **Implementation status (2026-07-09):** Implemented.
> Daily/monthly aggregation tables and retention cleanup exist; dashboard and rollup pages use these rollups.



Two data classes:

## 1. Raw data (event table)

Examples:

```none
Access Event
CrowdSec Event
Geoblock Event
Torblock Event
```

These are large.

Retention makes sense here:

`Default: 30 days`

configurable:

```none
7 days
30 days
90 days
180 days
365 days
unlimited
```


---

## 2. Aggregated data (statistics table)

These are small.

## Statistics table

Examples:

`daily_stats`

```json
{
  "date":"2026-06-19",
  "event_type":"geoblock",
  "count":842
}
```

Or:

```none
Day: 2026-06-19

Access:
12534

Geoblocks:
842

Torblocks:
37

CrowdSec Bans:
11
```

Or:

```none
Month: 2026-06

DE: 1284
RU: 932
CN: 842
US: 321
```


---

Or:

```none
Month: 2026-06

http-probing:
4321

http-sensitive-files:
2918

ssh-bf:
741
```

## Country table

`country_stats`


---

Example:

```json
{
  "month":"2026-06",
  "country":"RU",
  "count":932
}
```


---

## Scenario table

`scenario_stats`


---

Example:

```json
{
  "month":"2026-06",
  "scenario":"http-probing",
  "count":4321
}
```


---

## Heatmaps

`heatmap_country_daily`


---

Example:

```json
{
  "date":"2026-06-19",
  "country":"RU",
  "count":842
}
```


---
