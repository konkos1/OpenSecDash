import threading

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import settings


def configure_sqlite_pragmas(dbapi_connection, connection_record=None) -> None:
    # WAL lets readers (page views, and the periodic dashboard/diagnostics/
    # assets/banner polling) run concurrently with a writer instead of
    # blocking on it - the standard fix for "database is locked" once more
    # than one thing touches the database at a time, which datasource ticks
    # and the GeoIP backfill now do from real worker threads. synchronous=
    # NORMAL is the recommended pairing with WAL: still crash-safe, without
    # fsync-ing on every single commit.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


connect_args = {"check_same_thread": False, "timeout": 10} if settings.database_url.startswith("sqlite") else {}

# SQLite connections are cheap local file handles, not server-side resources
# like on Postgres/MySQL, so a bigger pool costs little here - it just avoids
# "QueuePool limit ... connection timed out" once datasource threads, the
# GeoIP backfill, and several browser tabs/auto-refreshing pages all want a
# connection at once. Scoped to SQLite only, since a bigger pool *would* cost
# real server-side resources against a client-server database.
pool_kwargs = {"pool_size": 10, "max_overflow": 20} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    **pool_kwargs,
)

if settings.database_url.startswith("sqlite") and ":memory:" not in settings.database_url:
    event.listens_for(engine, "connect")(configure_sqlite_pragmas)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# SQLite only ever allows one writer at a time. Without this, every place
# that writes (datasource plugins, GeoIP backfill, asset sync buttons,
# periodic loops, request handlers - which FastAPI runs in real threads for
# any non-async route) races every other one for that single writer lock and
# can outlast the busy_timeout with "database is locked". Hooking this into
# the Session class itself (rather than each call site) means every write
# transaction is serialized automatically, current and future, with nothing
# for individual plugins/routes/services to opt into.
write_lock = threading.Lock()

_WRITE_LOCK_HELD_KEY = "_opensecdash_write_lock_held"


def _acquire_write_lock_before_flush(session, flush_context, instances) -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    if session.info.get(_WRITE_LOCK_HELD_KEY):
        return  # already held for this transaction (e.g. an earlier explicit flush)
    write_lock.acquire()
    session.info[_WRITE_LOCK_HELD_KEY] = True


def _release_write_lock_after_transaction_end(session, transaction) -> None:
    # Using after_transaction_end (rather than after_commit/after_rollback)
    # is deliberate: a session that flushes and is then just closed - without
    # an explicit commit() or rollback() - fires neither of those, which
    # would leak the lock forever and deadlock every future writer. This
    # fires for every transaction end, commit/rollback/close alike.
    if transaction.parent is not None:
        return  # a nested/savepoint transaction ending isn't the real end
    if session.info.pop(_WRITE_LOCK_HELD_KEY, False):
        write_lock.release()


if settings.database_url.startswith("sqlite"):
    event.listens_for(Session, "before_flush")(_acquire_write_lock_before_flush)
    event.listens_for(Session, "after_transaction_end")(_release_write_lock_after_transaction_end)
