from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

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
