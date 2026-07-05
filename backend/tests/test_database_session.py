import sqlite3
import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.database.session import configure_sqlite_pragmas, write_lock
from app.models.settings import Setting


def test_configure_sqlite_pragmas_enables_wal_and_synchronous_normal(tmp_path):
    db_path = tmp_path / "pragma_test.db"
    connection = sqlite3.connect(str(db_path))
    try:
        configure_sqlite_pragmas(connection)

        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]

        assert journal_mode.lower() == "wal"
        # SQLite reports synchronous as an integer: 0=OFF, 1=NORMAL, 2=FULL.
        assert synchronous == 1
    finally:
        connection.close()


def test_write_lock_is_held_during_flush_and_released_after_commit(db_session):
    db_session.add(Setting(key="a", value="1"))
    db_session.flush()
    assert write_lock.locked() is True

    db_session.commit()
    assert write_lock.locked() is False


def test_write_lock_is_released_after_rollback(db_session):
    db_session.add(Setting(key="b", value="1"))
    db_session.flush()
    assert write_lock.locked() is True

    db_session.rollback()
    assert write_lock.locked() is False


def test_write_lock_is_released_when_session_closes_without_commit(db_session):
    # A session that flushes and is then discarded without an explicit
    # commit()/rollback() must not leak the lock forever - that would
    # deadlock every future writer in the process.
    db_session.add(Setting(key="c", value="1"))
    db_session.flush()
    assert write_lock.locked() is True

    db_session.close()
    assert write_lock.locked() is False


def test_write_lock_covers_bulk_query_delete_and_update(db_session):
    # query(...).delete() / .update() write via Session.execute() without a
    # flush, so they'd bypass a flush-only hook entirely - this bit the GeoIP
    # cache cleanup in production ("database is locked" during an import).
    db_session.add(Setting(key="bulk", value="1"))
    db_session.commit()

    db_session.query(Setting).filter(Setting.key == "bulk").update({"value": "2"})
    assert write_lock.locked() is True
    db_session.commit()
    assert write_lock.locked() is False

    db_session.query(Setting).filter(Setting.key == "bulk").delete()
    assert write_lock.locked() is True
    db_session.commit()
    assert write_lock.locked() is False


def test_plain_select_does_not_touch_the_write_lock(db_session):
    db_session.query(Setting).all()
    assert write_lock.locked() is False


def test_write_lock_is_not_reacquired_across_multiple_flushes_in_one_transaction(db_session):
    # A single write transaction (e.g. import_json_assets, which flushes
    # mid-loop to get a new row's id before the final commit) must only
    # acquire the lock once - re-acquiring on a later flush within the same
    # transaction would deadlock against itself.
    db_session.add(Setting(key="d", value="1"))
    db_session.flush()
    db_session.add(Setting(key="e", value="1"))
    db_session.flush()  # would deadlock here if the lock were re-acquired
    db_session.commit()
    assert write_lock.locked() is False


def test_concurrent_writers_are_serialized_by_the_write_lock():
    # A second thread's write must block on write_lock, not race the first
    # thread's still-open transaction, and must succeed once it releases.
    # Needs a real shared connection across threads (StaticPool), unlike the
    # db_session fixture's plain ":memory:" engine, which is thread-local.
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    LocalSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = LocalSessionLocal()
    db.add(Setting(key="f", value="1"))
    db.flush()
    assert write_lock.locked() is True

    second_thread_done = threading.Event()

    def second_writer():
        other_db = LocalSessionLocal()
        try:
            other_db.add(Setting(key="g", value="1"))
            other_db.commit()
        finally:
            other_db.close()
        second_thread_done.set()

    thread = threading.Thread(target=second_writer)
    thread.start()
    thread.join(timeout=0.5)
    assert not second_thread_done.is_set()  # still blocked on the first transaction's lock

    db.commit()
    db.close()
    thread.join(timeout=5)
    assert second_thread_done.is_set()

    engine.dispose()
