import asyncio
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.plugins import manager as manager_module
from app.plugins.manager import PluginManager, run_with_session


@pytest.fixture()
def recording_session_factory(monkeypatch):
    """Replace the loop session factory and record which thread closed a session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    closed_by: list[int] = []

    def recording_session():
        session = session_factory()
        original_close = session.close

        def close_and_record():
            closed_by.append(threading.get_ident())
            original_close()

        session.close = close_and_record  # type: ignore[method-assign]
        return session

    monkeypatch.setattr(manager_module, "SessionLocal", recording_session)
    try:
        yield closed_by
    finally:
        engine.dispose()


def test_run_with_session_closes_its_session_on_the_calling_thread(recording_session_factory):
    closed_by = recording_session_factory
    used_by: list[int] = []

    def work(db, marker: str) -> str:
        used_by.append(threading.get_ident())
        db.execute(text("select 1"))
        return marker

    assert asyncio.run(asyncio.to_thread(run_with_session, work, "done")) == "done"
    assert len(closed_by) == 1
    assert closed_by == used_by


def test_run_with_session_closes_its_session_when_the_work_fails(recording_session_factory):
    closed_by = recording_session_factory

    def failing_work(_db):
        raise RuntimeError("tick failed")

    with pytest.raises(RuntimeError):
        run_with_session(failing_work)
    assert len(closed_by) == 1


def test_cancelling_a_loop_never_closes_a_session_its_worker_thread_still_uses(recording_session_factory):
    """Regression: cancelling a loop used to close the session mid-commit.

    ``asyncio.to_thread`` cannot stop a thread that is already running, so a
    session owned by the event loop was closed while the worker was still
    inside ``commit()`` - SQLAlchemy raised ``IllegalStateChangeError``.
    """
    closed_by = recording_session_factory
    tick_started = threading.Event()
    may_finish = threading.Event()
    tick_finished = threading.Event()
    used_by: list[int] = []
    failures: list[BaseException] = []

    def blocking_dispatch(db) -> int:
        used_by.append(threading.get_ident())
        tick_started.set()
        may_finish.wait(5)
        try:
            # The session must still be usable: nothing may have closed it
            # while this thread was working.
            db.execute(text("select 1"))
            db.commit()
        except BaseException as exc:  # noqa: BLE001 - the test reports any failure
            failures.append(exc)
        tick_finished.set()
        return 0

    monkeypatch_target = manager_module.dispatch_pending_notifications

    async def scenario():
        manager_module.dispatch_pending_notifications = blocking_dispatch
        try:
            manager = PluginManager(Path("plugins"))
            task = asyncio.create_task(manager._notification_dispatch_loop())
            await asyncio.to_thread(tick_started.wait, 5)
            assert tick_started.is_set()

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            # The cancelled loop must not have touched the worker's session.
            assert closed_by == []
            may_finish.set()
            await asyncio.to_thread(tick_finished.wait, 5)
        finally:
            manager_module.dispatch_pending_notifications = monkeypatch_target

    asyncio.run(scenario())

    assert failures == []
    assert tick_finished.is_set()
    assert closed_by == used_by
