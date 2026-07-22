"""Explicit million-event benchmark for Events and Access searches.

Run from ``backend/``. The fixture always lives in a temporary directory and is
deleted after the run, so this helper cannot accidentally benchmark or modify a
development database.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import resource
import sqlite3
import statistics
import tempfile
import time
from typing import Any, cast

from sqlalchemy import Table, create_engine, text
from sqlalchemy.orm import Session

from app.models.events import Event
from app.services.events import apply_event_filters


DEFAULT_EVENT_COUNT = 1_000_000
INSERT_BATCH_SIZE = 10_000


def _event_rows(count: int, end_time: datetime) -> Iterator[tuple[object, ...]]:
    plugins = ("traefik_log", "crowdsec", "geoblock_log")
    event_types = ("access.allowed", "access.error", "security.ban", "security.geoblock")
    paths = ("/", "/health", "/api/status", "/assets/app.js", "/wp-login.php")
    hostnames = ("proxy.example.test", "cloud.example.test", "home.example.test")
    countries = ("DE", "NL", "US", "FR", "GB")
    start_time = end_time - timedelta(days=30)
    for index in range(count):
        event_time = start_time + timedelta(seconds=(index * 2_592_000) // count)
        event_time_text = event_time.isoformat(sep=" ")
        path = paths[index % len(paths)]
        hostname = hostnames[index % len(hostnames)]
        ip = f"198.51.{(index // 250) % 100}.{index % 250 + 1}"
        status_code = 404 if path == "/wp-login.php" else (500 if index % 97 == 0 else 200)
        payload = {"request_id": index, "router_name": "web-secure"}
        if index % 250 == 0:
            payload["details"] = "x" * 2048
        yield (
            event_time_text,
            event_time_text,
            event_time_text,
            "benchmark",
            "benchmark",
            plugins[index % len(plugins)],
            plugins[index % len(plugins)],
            event_types[index % len(event_types)],
            "warning" if status_code >= 400 else "info",
            ip,
            countries[index % len(countries)],
            "Example City",
            f"AS{64500 + index % 20}",
            "Example ISP",
            hostname,
            "GET",
            path,
            status_code,
            json.dumps(payload),
            f'{ip} {hostname} "GET {path}" {status_code}',
            "raw",
            1,
            0,
        )


def _populate_database(database_path: Path, count: int, end_time: datetime) -> tuple[float, float]:
    engine = create_engine(f"sqlite:///{database_path}")
    cast(Table, Event.__table__).create(engine)
    engine.dispose()

    columns = (
        "timestamp, created_at, event_time, source, source_id, plugin, plugin_id, "
        "event_type, severity, ip, country, city, asn, isp, hostname, method, path, "
        "status_code, data_json, raw_data, retention_class, geoip_checked, is_local_ip"
    )
    placeholders = ", ".join("?" for _ in range(23))
    started = time.perf_counter()
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=OFF")
        rows = _event_rows(count, end_time)
        while pending_batch := list(next(rows, None) for _ in range(INSERT_BATCH_SIZE)):
            batch = [row for row in pending_batch if row is not None]
            if not batch:
                break
            connection.executemany(
                f"INSERT INTO events ({columns}) VALUES ({placeholders})",  # noqa: S608 - fixed schema statement
                batch,
            )
            connection.commit()
            if len(batch) < INSERT_BATCH_SIZE:
                break
    elapsed = time.perf_counter() - started
    size_mib = database_path.stat().st_size / (1024 * 1024)
    return elapsed, size_mib


def _percentile_95(values: list[float]) -> float:
    if len(values) < 2:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def _benchmark_query(
    session: Session,
    engine,
    filters: dict[str, Any],
    iterations: int,
) -> dict[str, Any]:
    def query():
        return (
            apply_event_filters(session.query(Event), filters)
            .order_by(Event.event_time.desc())
            .limit(200)
        )

    query().all()
    durations = []
    result_count = 0
    for _ in range(iterations):
        started = time.perf_counter()
        result_count = len(query().all())
        durations.append((time.perf_counter() - started) * 1000)

    statement = query().statement.compile(engine, compile_kwargs={"literal_binds": True})
    plan = [row[-1] for row in session.execute(text(f"EXPLAIN QUERY PLAN {statement}"))]
    return {
        "p95_ms": round(_percentile_95(durations), 2),
        "min_ms": round(min(durations), 2),
        "rows": result_count,
        "query_plan": plan,
    }


def run(event_count: int, iterations: int, default_range: str) -> dict[str, Any]:
    end_time = datetime(2026, 7, 22, 12)
    with tempfile.TemporaryDirectory(prefix="opensecdash-search-benchmark-") as directory:
        database_path = Path(directory) / "events.db"
        populate_seconds, database_mib = _populate_database(database_path, event_count, end_time)

        startup_started = time.perf_counter()
        engine = create_engine(f"sqlite:///{database_path}")
        with Session(engine) as session:
            session.execute(text("SELECT 1")).scalar_one()
            startup_ms = (time.perf_counter() - startup_started) * 1000
            recent = end_time - timedelta(hours=24)
            default_filters = {"event_time_from": recent} if default_range == "24h" else {}
            cases = {
                "events_initial": default_filters,
                "access_initial": {
                    "event_type": "access.*",
                    "plugins": ["traefik_log"],
                    **default_filters,
                },
                "structured_window": {
                    "country": "DE",
                    "status_code_min": 400,
                    "event_time_from": recent,
                },
                "search_no_match": {"q": "definitely-no-such-event", **default_filters},
                "search_path": {"q": "/wp-login.php", **default_filters},
                "search_hostname": {"q": "cloud.example.test", **default_filters},
                "search_ip": {"q": "198.51.10.25", **default_filters},
                "search_combined": {
                    "q": '("/wp-login.php" && 404) || (cloud && 500)',
                    **default_filters,
                },
            }
            results = {
                name: _benchmark_query(session, engine, filters, iterations)
                for name, filters in cases.items()
            }
        engine.dispose()

        rss_divisor = 1024 * 1024 if os.uname().sysname == "Darwin" else 1024
        return {
            "events": event_count,
            "database_mib": round(database_mib, 2),
            "populate_seconds": round(populate_seconds, 2),
            "startup_ms": round(startup_ms, 2),
            "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rss_divisor, 2),
            "iterations": iterations,
            "default_range": default_range,
            "results": results,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=DEFAULT_EVENT_COUNT)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--default-range", choices=("24h", "all"), default="24h")
    arguments = parser.parse_args()
    if arguments.events < 1:
        parser.error("--events must be positive")
    if arguments.iterations < 1:
        parser.error("--iterations must be positive")
    print(json.dumps(run(arguments.events, arguments.iterations, arguments.default_range), indent=2))


if __name__ == "__main__":
    main()
