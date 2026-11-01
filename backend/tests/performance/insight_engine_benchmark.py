"""Explicit scratch-only benchmark; run the file from backend with PYTHONPATH set to that directory.

Example: PYTHONPATH=. .venv/bin/python tests/performance/insight_engine_benchmark.py seed --db /tmp/osd-bench/fixture.db
Run ingest, correlate and read in separate processes to keep fixture RSS out of app measurements.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import json
import logging
import math
import os
from pathlib import Path
import platform
import random
import resource
import sqlite3
import statistics
import tempfile
import time
from typing import Any
from unittest.mock import patch

# Validation precedes application imports, including the module-level database engine.
ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 1, 12)
SEED = 90210
EVENTS = 1_000_000
INSIGHTS = 100_000
BATCH = 2_000
COUNTRIES = ("DE", "FR", "NL", "GB", "US", "CA", "AU", "JP", "SE", "FI")
MARKER = "insights.benchmark.fixture"


def scratch_path(value: str) -> Path:
    """Reject persistent locations, symlinks and the normal development database."""
    candidate = Path(value).expanduser().absolute()
    resolved = candidate.resolve()
    temporary = (Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve())
    if candidate.is_symlink() or not any(resolved.is_relative_to(root) for root in temporary) or resolved.is_relative_to(ROOT):
        raise ValueError("Database must be a scratch file outside the repository under the system temporary directory")
    if resolved.name == "opensecdash.db" or resolved.suffix != ".db":
        raise ValueError("Use a distinct scratch .db filename, never opensecdash.db")
    return resolved


def percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def peak_mib() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024 if platform.system() == "Darwin" else 1024)


def seed(path: Path) -> dict[str, Any]:
    """Build fixed-size synthetic data in bounded batches, never overwrite a database."""
    from sqlalchemy import insert
    from app.database.base import Base
    from app.database.session import engine, SessionLocal
    from app.models import Asset, Event, Insight, Setting, System
    from app.services.insight_rules import import_ruleset
    from app.services.insight_trends import run_trend_tick

    if path.exists():
        raise ValueError("Seed requires a new scratch database")
    path.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    rng = random.Random(SEED)
    with SessionLocal() as db:
        db.add(System(id=1, vmid="example", hostname="host.example"))
        db.flush()
        db.add_all([Asset(id=i, system_id=1, name=f"Service {i}", hostname=f"service-{i}.example") for i in range(1, 51)])
        db.add_all([Setting(key=f"plugin.{name}.enabled", value="true") for name in ("traefik_log", "crowdsec", "geoblock_log")])
        db.add(Setting(key=MARKER, value=str(SEED)))
        db.commit()
        run_trend_tick(db, now=NOW.replace(hour=0) - timedelta(days=14))
        chain = json.loads((ROOT / "backend/tests/fixtures/insight-level2.json").read_text())["rules"][0]
        rules = [{**chain, "id": f"bench.chain.{i:02}"} for i in range(20)]
        rules += [{"id": f"bench.probe.{i:03}", "title": "Synthetic probe", "event_types": ["access.error"],
            "path_contains_any": ["/wp-login.php" if i == 0 else f"/unused-{i}"], "group_by": "path",
            "threshold": 20, "window_minutes": 5} for i in range(180)]
        import_ruleset(db, {"schema_version": 2, "ruleset_version": "2026-11-01", "rules": rules}, source="bundled")
        for offset in range(0, EVENTS, BATCH):
            rows = []
            for i in range(offset, min(offset + BATCH, EVENTS)):
                # Most requests are irrelevant; a hot family and 201 anchors exercise aggregation and resume.
                anchor = i >= EVENTS - 201
                hot = i % 10 == 0 or EVENTS - 401 <= i < EVENTS - 201
                stamp = NOW - timedelta(seconds=rng.randrange(14 * 86400))
                if i >= EVENTS - 401:
                    stamp = NOW - timedelta(seconds=1 if not anchor else 0)
                rows.append(dict(id=i + 1, event_type="security.ban" if anchor else "access.error" if hot else "access.ok",
                    event_time=stamp, timestamp=stamp, created_at=NOW if anchor else stamp,
                    plugin="crowdsec" if anchor else "traefik_log", ip=f"192.0.2.{i % 200 + 1}",
                    asset_id=i % 50 + 1, country=COUNTRIES[i % 10], hostname=f"service-{i % 50 + 1}.example",
                    path="/wp-login.php" if hot else "/index.html", method="GET", status_code=404 if hot else 200,
                    geoip_checked=True, is_local_ip=False))
            db.execute(insert(Event), rows)
            db.commit()
        for offset in range(0, INSIGHTS, BATCH):
            rows = [dict(id=i + 1, type=f"bench.legacy.{i % 100}", title="Synthetic retained finding",
                timestamp=NOW - timedelta(seconds=i), occurred_at=NOW - timedelta(seconds=i) if i % 2 else None,
                ip=f"198.51.100.{i % 200 + 1}", correlation_key=f"ip:198.51.100.{i % 200 + 1}")
                for i in range(offset, min(offset + BATCH, INSIGHTS))]
            db.execute(insert(Insight), rows)
            db.commit()
    return {"events": EVENTS, "insights": INSIGHTS, "assets": 50, "countries": 10, "rules": 200, "chains": 20}


class Probe:
    """Count SQL and retain bounded query plans without recording parameter values."""

    def __init__(self, engine: Any):
        self.engine = engine
        self.count = 0
        self.plans: dict[str, list[str]] = {}
        self.batches: dict[str, int] = {}
        self.stream_batch = 0
        self.patch: Any = None

    def before(self, connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        if statement.lstrip().upper().startswith("EXPLAIN"):
            return
        self.count += 1
        self.stream_batch = max(self.stream_batch, context.execution_options.get("yield_per", 0))
        if statement.lstrip().upper().startswith("SELECT") and ("FROM events" in statement or "FROM insights" in statement):
            if statement not in self.plans:
                self.plans[statement] = [str(row[-1]) for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters)]

    def start(self) -> None:
        from sqlalchemy import event
        from sqlalchemy.orm import Query
        original = Query.all

        def measured(query: Any) -> Any:
            rows = original(query)
            statement = str(query.statement)
            table = "events" if "FROM events" in statement else "insights" if "FROM insights" in statement else "other"
            self.batches[table] = max(self.batches.get(table, 0), len(rows))
            return rows

        self.patch = patch.object(Query, "all", measured)
        self.patch.start()
        event.listen(self.engine, "before_cursor_execute", self.before)

    def stop(self) -> None:
        from sqlalchemy import event
        event.remove(self.engine, "before_cursor_execute", self.before)
        self.patch.stop()


def ingest(scenario: str, repeats: int, count: int) -> dict[str, Any]:
    from app.database.session import engine, SessionLocal
    from app.services.events import store_event

    probe = Probe(engine)
    rounds = []
    for run in range(repeats + 1):
        latencies = []
        probe.start()
        before = probe.count
        started = time.perf_counter()
        try:
            with SessionLocal() as db:
                for index in range(count):
                    tick = time.perf_counter()
                    store_event(db, event_type="access.error" if scenario == "hot" else "access.ok", plugin="traefik_log", ip=f"203.0.113.{index % 200 + 1}",
                        asset_id=1, country="DE", path="/wp-login.php" if scenario == "hot" else "/irrelevant",
                        method="GET", status_code=404 if scenario == "hot" else 200, event_time=NOW + timedelta(seconds=index))
                    latencies.append((time.perf_counter() - tick) * 1000)
                # Each repetition sees identical committed state.
                db.rollback()
        finally:
            probe.stop()
        duration = time.perf_counter() - started
        if run:
            rounds.append({"events_per_second": count / duration, "p50_ms": statistics.median(latencies),
                "p95_ms": percentile(latencies, .95), "sql_per_event": (probe.count - before) / count})
    return {"scenario": scenario, "events_per_run": count, "rounds": rounds, "median_events_per_second": statistics.median(r["events_per_second"] for r in rounds),
        "query_plans": probe.plans, "largest_loaded_batches": probe.batches, "max_stream_fetch_size": probe.stream_batch}


async def correlate(repeats: int) -> dict[str, Any]:
    import httpx
    from app.database.session import engine, SessionLocal
    from app.main import app
    from app.models.core import Insight
    from app.models.settings import Setting
    from app.services.insight_correlation import CYCLE_KEY, run_level2_tick
    from app.services.insight_trends import CYCLE_KEY as TREND_CYCLE, run_trend_tick
    from app.services.insight_catalog import available_insight_types
    from app.services.insight_rules import active_rules

    logging.getLogger("httpx").setLevel(logging.WARNING)
    with SessionLocal() as db:
        available = available_insight_types(db)
        chains = [rule for rule in active_rules(db) if rule.kind == "scan_ban_chain" and rule.id in available]
        if len(chains) != 20:
            raise RuntimeError("The benchmark requires all 20 chain rules to be available")
    probe = Probe(engine)
    rounds = []
    warmup: dict[str, Any] = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://benchmark.example") as client:
        for run in range(repeats + 1):
            # Preserve persisted findings (normal warm dedupe), restart cursors for a full fixed workload.
            with SessionLocal() as db:
                db.query(Setting).filter(Setting.key.in_((CYCLE_KEY, TREND_CYCLE))).delete(synchronize_session=False)
                db.commit()
            readiness = []

            def work() -> dict[str, Any]:
                ticks = []
                with SessionLocal() as db:
                    while True:
                        started = time.perf_counter()
                        result = run_level2_tick(db, now=NOW)
                        ticks.append({**result, "seconds": time.perf_counter() - started})
                        if result["completed"]:
                            break
                    started = time.perf_counter()
                    trend = run_trend_tick(db, now=NOW)
                    return {"chain_ticks": ticks, "chain_findings": db.query(Insight).filter(Insight.type.like("bench.chain.%")).count(), "trend": trend, "trend_seconds": time.perf_counter() - started}

            before = probe.count
            probe.start()
            try:
                worker = asyncio.create_task(asyncio.to_thread(work))
                while not worker.done():
                    started = time.perf_counter()
                    response = await client.get("/ready")
                    if response.status_code != 200:
                        raise RuntimeError("Readiness failed")
                    readiness.append((time.perf_counter() - started) * 1000)
                    await asyncio.sleep(.01)
                result = await worker
            finally:
                probe.stop()
            result.update(sql_statements=probe.count - before, readiness_samples=len(readiness), readiness_p95_ms=percentile(readiness, .95))
            if run:
                rounds.append(result)
            else:
                warmup = result
    return {"rounds": rounds, "warmup": warmup, "query_plans": probe.plans, "largest_loaded_batches": probe.batches, "max_stream_fetch_size": probe.stream_batch}


def read(repeats: int) -> dict[str, Any]:
    from app.api.insights import export_insights
    from app.database.session import engine, SessionLocal
    from app.models.core import Insight
    from app.services.insight_export import serialize_insight
    from app.services.insight_rules import _insight_in_cooldown

    probe = Probe(engine)
    rounds = []
    for run in range(repeats + 1):
        probe.start()
        started = time.perf_counter()
        try:
            with SessionLocal() as db:
                _insight_in_cooldown(db, "bench.legacy.1", correlation_key="ip:198.51.100.2",
                    window_start=NOW - timedelta(minutes=5), window_end=NOW)
                db.query(Insight).filter(Insight.type == "bench.legacy.1", Insight.ip == "198.51.100.2",
                    Insight.timestamp >= NOW - timedelta(minutes=5), Insight.timestamp <= NOW).first()
                response = export_insights(db, start="2026-10-31T12:00:00Z", end="2026-11-01T12:01:00Z", limit=500)
                data = json.loads(bytes(response.body))
                second = export_insights(db, cursor=data["next_cursor"], limit=500)
                mqtt = [serialize_insight(row) for row in db.query(Insight).filter(Insight.id > 99_900).order_by(Insight.id).limit(100).all()]
                result = {"json_items": len(data["items"]), "next_page_items": len(json.loads(bytes(second.body))["items"]),
                    "mqtt_serialized": len(mqtt), "seconds": time.perf_counter() - started}
        finally:
            probe.stop()
        if run:
            rounds.append(result)
    return {"rounds": rounds, "query_plans": probe.plans, "largest_loaded_batches": probe.batches, "max_stream_fetch_size": probe.stream_batch}


def main() -> None:
    """Run only on an explicit, marked scratch database and emit a JSON measurement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("seed", "ingest", "correlate", "read"))
    parser.add_argument("--db", required=True)
    parser.add_argument("--scenario", choices=("irrelevant", "hot"), default="irrelevant")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--events-per-run", type=int, default=100)
    args = parser.parse_args()
    path = scratch_path(args.db)
    if args.repeats < 5 or args.events_per_run < 1:
        parser.error("At least five repetitions and a positive event count are required")
    if args.mode != "seed":
        if not path.is_file():
            parser.error("Seed the scratch database first")
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (MARKER,)).fetchone()
            if row != (str(SEED),):
                parser.error("Not a marked benchmark scratch database")
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    from app.plugins.manager import get_plugin_manager
    get_plugin_manager()
    started = time.perf_counter()
    if args.mode == "seed":
        result = seed(path)
    elif args.mode == "ingest":
        result = ingest(args.scenario, args.repeats, args.events_per_run)
    elif args.mode == "correlate":
        result = asyncio.run(correlate(args.repeats))
    else:
        result = read(args.repeats)
    from app.database.session import engine
    engine.dispose()
    gates: dict[str, bool] = {}
    if args.mode != "seed":
        gates["app_rss_below_512_mib"] = peak_mib() < 512
    if args.mode == "correlate":
        gates["warm_100_anchor_ticks_below_5_seconds"] = all(tick["seconds"] < 5
            for row in result["rounds"] for tick in row["chain_ticks"] if tick["processed"] == 100)
        gates["readiness_p95_below_250_ms"] = all(row["readiness_p95_ms"] < 250 for row in result["rounds"])
        gates["finite_chain_findings"] = all(row["chain_findings"] == 20 for row in result["rounds"])
        gates["complete_201_anchor_cursor"] = all(sum(t["processed"] for t in row["chain_ticks"]) == 201
            and row["chain_ticks"][-1]["completed"] for row in result["rounds"])
    print(json.dumps({"mode": args.mode, "gates": gates, "seed": SEED, "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        "machine": platform.machine(), "platform": platform.platform(), "logical_cpus": os.cpu_count(),
        "warmup": 0 if args.mode == "seed" else 1, "repeats": 0 if args.mode == "seed" else args.repeats,
        "peak_rss_mib": peak_mib(), "db_bytes": path.stat().st_size, "seconds": time.perf_counter() - started,
        "result": result}, indent=2))
    if not all(gates.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
