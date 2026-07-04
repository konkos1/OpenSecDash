import sqlite3

from app.database.session import configure_sqlite_pragmas


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
