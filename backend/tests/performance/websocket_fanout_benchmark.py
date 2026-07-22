"""Compare per-client polling with the app-wide event broadcaster.

The script uses a temporary SQLite database and reports actual SELECT counts
and worker-thread IDs for 1, 10, and 50 simulated clients.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import tempfile
import threading

from app.services.event_broadcaster import EventBroadcaster


POLL_TICKS = 5
SHARED_INTERVAL_SECONDS = 0.01


class ScratchPoller:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.Lock()
        self.query_count = 0
        self.thread_ids: set[int] = set()

    def poll(self) -> tuple[bool, int]:
        with sqlite3.connect(self.database_path) as connection:
            enabled = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("plugin.traefik_log.enabled",),
            ).fetchone()
            last_event_id = connection.execute("SELECT max(id) FROM events").fetchone()
        with self._lock:
            self.query_count += 2
            self.thread_ids.add(threading.get_ident())
        return enabled == ("true",), int((last_event_id or (0,))[0] or 0)


async def _legacy_measure(poller: ScratchPoller, clients: int) -> dict[str, int]:
    for _ in range(POLL_TICKS):
        await asyncio.gather(*(asyncio.to_thread(poller.poll) for _ in range(clients)))
    return {
        "queries": poller.query_count,
        "threads": len(poller.thread_ids),
    }


async def _shared_measure(poller: ScratchPoller, clients: int) -> dict[str, int]:
    broadcaster = EventBroadcaster(poller.poll, interval_seconds=SHARED_INTERVAL_SECONDS)
    subscribers = [broadcaster.subscribe() for _ in range(clients)]
    await broadcaster.start()
    await asyncio.sleep(SHARED_INTERVAL_SECONDS * (POLL_TICKS - 1) + 0.005)
    await broadcaster.stop()
    for subscriber in subscribers:
        broadcaster.unsubscribe(subscriber)
    return {
        "queries": poller.query_count,
        "threads": len(poller.thread_ids),
    }


async def run() -> dict[str, dict[str, dict[str, int]]]:
    results: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    with tempfile.TemporaryDirectory(prefix="opensecdash-websocket-benchmark-") as directory:
        database_path = Path(directory) / "fanout.db"
        with sqlite3.connect(database_path) as connection:
            connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO settings VALUES (?, ?)", ("plugin.traefik_log.enabled", "true"))
            connection.execute("INSERT INTO events DEFAULT VALUES")
        for clients in (1, 10, 50):
            results[str(clients)]["legacy"] = await _legacy_measure(ScratchPoller(database_path), clients)
            results[str(clients)]["broadcaster"] = await _shared_measure(ScratchPoller(database_path), clients)
    return dict(results)


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
