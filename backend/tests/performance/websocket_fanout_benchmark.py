"""Compare per-client polling with the app-wide event broadcaster.

The script also includes the per-client session validation that the runtime
performs every five seconds, so the remaining linear database load is visible.
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
        self.poll_query_count = 0
        self.session_query_count = 0
        self.thread_ids: set[int] = set()

    def poll(self) -> tuple[bool, int]:
        with sqlite3.connect(self.database_path) as connection:
            enabled = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("plugin.traefik_log.enabled",),
            ).fetchone()
            last_event_id = connection.execute("SELECT max(id) FROM events").fetchone()
        with self._lock:
            self.poll_query_count += 2
            self.thread_ids.add(threading.get_ident())
        return enabled == ("true",), int((last_event_id or (0,))[0] or 0)

    def validate_session(self, client_id: int) -> bool:
        with sqlite3.connect(self.database_path) as connection:
            onboarding = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("auth.onboarding_state",),
            ).fetchone()
            auth_enabled = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("auth.enabled",),
            ).fetchone()
            hostname = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("auth.hostname",),
            ).fetchone()
            session = connection.execute(
                "SELECT user_id FROM user_sessions WHERE token = ?",
                (f"token-{client_id}",),
            ).fetchone()
            user = connection.execute(
                "SELECT is_active FROM users WHERE id = ?",
                (session[0] if session else -1,),
            ).fetchone()
        with self._lock:
            self.session_query_count += 5
            self.thread_ids.add(threading.get_ident())
        return onboarding == ("complete",) and auth_enabled == ("true",) and hostname == ("dash.example",) and user == (1,)

    def result(self) -> dict[str, int]:
        return {
            "poll_queries": self.poll_query_count,
            "session_validation_queries": self.session_query_count,
            "total_queries": self.poll_query_count + self.session_query_count,
            "threads": len(self.thread_ids),
        }


async def _legacy_measure(poller: ScratchPoller, clients: int) -> dict[str, int]:
    for _ in range(POLL_TICKS):
        await asyncio.gather(
            *(asyncio.to_thread(poller.poll) for _ in range(clients)),
            *(asyncio.to_thread(poller.validate_session, client_id) for client_id in range(clients)),
        )
    return poller.result()


async def _shared_measure(poller: ScratchPoller, clients: int) -> dict[str, int]:
    broadcaster = EventBroadcaster(poller.poll, interval_seconds=SHARED_INTERVAL_SECONDS)
    subscribers = [broadcaster.subscribe() for _ in range(clients)]
    await broadcaster.start()
    await asyncio.sleep(SHARED_INTERVAL_SECONDS * (POLL_TICKS - 1) + 0.005)
    await broadcaster.stop()
    for _ in range(POLL_TICKS):
        await asyncio.gather(*(asyncio.to_thread(poller.validate_session, client_id) for client_id in range(clients)))
    for subscriber in subscribers:
        broadcaster.unsubscribe(subscriber)
    return poller.result()


async def run() -> dict[str, dict[str, dict[str, int]]]:
    results: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    with tempfile.TemporaryDirectory(prefix="opensecdash-websocket-benchmark-") as directory:
        database_path = Path(directory) / "fanout.db"
        with sqlite3.connect(database_path) as connection:
            connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
            connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, is_active INTEGER NOT NULL)")
            connection.execute("CREATE TABLE user_sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL)")
            connection.execute("INSERT INTO settings VALUES (?, ?)", ("plugin.traefik_log.enabled", "true"))
            connection.execute("INSERT INTO settings VALUES (?, ?)", ("auth.onboarding_state", "complete"))
            connection.execute("INSERT INTO settings VALUES (?, ?)", ("auth.enabled", "true"))
            connection.execute("INSERT INTO settings VALUES (?, ?)", ("auth.hostname", "dash.example"))
            connection.execute("INSERT INTO events DEFAULT VALUES")
            connection.execute("INSERT INTO users VALUES (?, ?)", (1, 1))
            connection.executemany(
                "INSERT INTO user_sessions VALUES (?, ?)",
                ((f"token-{client_id}", 1) for client_id in range(50)),
            )
        for clients in (1, 10, 50):
            results[str(clients)]["legacy"] = await _legacy_measure(ScratchPoller(database_path), clients)
            results[str(clients)]["broadcaster"] = await _shared_measure(ScratchPoller(database_path), clients)
    return dict(results)


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
