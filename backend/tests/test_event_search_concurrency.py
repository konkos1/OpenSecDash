from datetime import datetime, timedelta
import threading
from typing import cast

from sqlalchemy import Table, create_engine, event as sqlalchemy_event, func
from sqlalchemy.orm import Session

from app.database.session import configure_sqlite_pragmas
from app.models.events import Event
from app.services.events import apply_event_filters


def test_search_load_does_not_block_parallel_event_ingestion(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'search-concurrency.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    sqlalchemy_event.listen(engine, "connect", configure_sqlite_pragmas)
    event_table = cast(Table, Event.__table__)
    event_table.create(engine)
    now = datetime(2026, 7, 22, 12)
    with engine.begin() as connection:
        connection.execute(
            event_table.insert(),
            [
                {
                    "event_time": now - timedelta(seconds=index),
                    "timestamp": now - timedelta(seconds=index),
                    "event_type": "access.allowed",
                    "plugin": "traefik_log",
                    "path": f"/request/{index}",
                }
                for index in range(5_000)
            ],
        )

    errors: list[Exception] = []
    start = threading.Event()

    def ingest() -> None:
        try:
            start.wait()
            with Session(engine) as session:
                session.add_all(
                    Event(
                        event_time=now + timedelta(seconds=index),
                        timestamp=now + timedelta(seconds=index),
                        event_type="access.allowed",
                        plugin="traefik_log",
                        path=f"/ingested/{index}",
                    )
                    for index in range(200)
                )
                session.commit()
        except Exception as exc:
            errors.append(exc)

    writer = threading.Thread(target=ingest)
    writer.start()
    start.set()
    try:
        with Session(engine) as session:
            for _ in range(20):
                apply_event_filters(
                    session.query(Event),
                    {"q": "no-such-event", "event_time_from": now - timedelta(hours=24)},
                ).limit(200).all()
        writer.join(timeout=10)
        assert not writer.is_alive()
        assert errors == []
        with Session(engine) as session:
            assert session.query(func.count(Event.id)).scalar() == 5_200
    finally:
        if writer.is_alive():
            writer.join(timeout=10)
        engine.dispose()
