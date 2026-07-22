# System validation benchmarks

These release checks are explicit because the million-event fixture is too large for
the normal unit-test job. The helpers create their SQLite databases below temporary
directories and delete them after the run. They never read `DATABASE_URL` or the
development database.

Run from `backend/`:

```bash
.venv/bin/python -m tests.performance.event_search_benchmark \
  --events 0 --iterations 5 --default-range 24h --enforce-gates
.venv/bin/python -m tests.performance.event_search_benchmark \
  --events 10000 --iterations 5 --default-range 24h --enforce-gates
.venv/bin/python -m tests.performance.event_search_benchmark \
  --events 1000000 --iterations 5 --default-range 24h --enforce-gates
.venv/bin/python -m tests.performance.upgrade_benchmark \
  --events 10000 --enforce-gates
.venv/bin/python -m tests.performance.websocket_fanout_benchmark
```

The Docker release workflow runs the same Fresh, Small, Large, and Upgrade profiles
inside the exact release-candidate image. Fresh and Small use 1 vCPU/512 MiB; Large and
Upgrade use 2 vCPU/1 GiB. Each profile writes a JSON report, and a failed gate prevents
image publication.

The search fixture covers three plugins, four event types, paths, public IPs,
countries, hostnames, ASNs, status codes, 30 days of timestamps, JSON payloads, raw
lines, and occasional 2 KiB payload values. Every case is warmed once and then run five
times; the report includes p50/p95, the SQLite query plan, database size, fixture build
time, connection/first-query startup time, process peak RSS, 100 serial and 20 parallel
readiness calls, connection cleanup, and a machine-readable gate result. The upgrade
profile builds a representative supported legacy schema from the first migration,
preserves synthetic legacy events, migrates secrets, runs startup maintenance,
confirms auth remains off, and proves the second start skips the event scan.

## Results from 2026-07-22

Environment: macOS/Darwin arm64, Python 3.13, repository SQLite runtime. Times are local
wall-clock measurements and should be compared on the same host rather than treated as
portable hardware scores.

Baseline before bounded defaults and raw-data opt-in:

- fixture: 1,000,000 events, 1,046.41 MiB, 22.65 s build, 0.78 ms startup,
  90.81 MiB peak RSS;
- all-time Events list p95: 1.23 ms; Access list p95: 1.30 ms;
- structured 24-hour filter p95: 3.21 ms;
- all-time no-match search p95: 1,525.26 ms (full index scan);
- all-time IP search p95: 1,662.64 ms (full index scan);
- all-time path/hostname/combined searches p95: 2.29/1.84/3.95 ms because the
  newest 200 matches are found immediately.

After the bounded search changes:

| Case | 24-hour p95 | Explicit all-time p95 | Query-plan summary |
| --- | ---: | ---: | --- |
| Events list | 1.27 ms | 1.26 ms | `event_time` index / ordered index scan |
| Access list | 1.30 ms | 1.32 ms | `plugin,event_time` index |
| Structured window | 3.22 ms | 3.28 ms | `country,event_time` index |
| No match | 24.85 ms | 815.50 ms | bounded index search / explicit full scan |
| Path | 2.26 ms | 2.23 ms | `event_time` index / explicit full scan |
| Hostname | 1.85 ms | 1.96 ms | `event_time` index / explicit full scan |
| Exact IP | 0.16 ms | 0.36 ms | bound `ip` index predicate |
| Boolean/quotes | 3.59 ms | 3.56 ms | bounded index search / explicit full scan |

The final 24-hour fixture was 1,046.41 MiB, took 22.70 s to build and 0.73 ms to
open/execute its first query, with 90.38 MiB peak RSS. The explicit all-time run was
also checked: even its worst no-match case stayed below the 1,000 ms gate.

FTS5 is therefore not required. The bounded `LIKE` path meets the 250/750/1,000 ms
list/typical/no-match targets, including the explicit all-time no-match gate, without a
second index, backfill, trigger synchronization, retention coupling, or SQLite feature
fallback. The `unicode61`/`trigram` comparison is intentionally not activated because
the decision gate did not open.

## Fan-out results

The fan-out helper performs five polling ticks against a scratch SQLite database. Each
poll executes the plugin-setting query and `max(events.id)` query.

| Simulated clients | Legacy queries / worker threads | Broadcaster queries / worker threads |
| ---: | ---: | ---: |
| 1 | 10 / 1 | 10 / 1 |
| 10 | 100 / 10 | 8 / 4 |
| 50 | 500 / 14 | 10 / 5 |

Worker assignment varies with the Python executor, but the relevant result is stable:
legacy queries grow directly with client count while broadcaster queries remain one
poll stream. Every subscriber has a one-item queue, so a slow client retains only the
latest state and cannot block the poller or another client.

## Browser smoke from 2026-07-22

A local server used a scratch `DATABASE_URL` and a test-only wrapper that delayed every
HTMX Events refresh by 3.5 seconds. Three WebSocket-visible event changes produced one
active request and exactly one queued follow-up (`count=2`, `max_active=1`). After
switching the same browser tab to Snapshot, two further event changes left the counters
unchanged. This verifies bounded HTMX queueing and the frozen Snapshot behavior in a
real browser; HTMX owns request cleanup for success, error, and abort paths.
