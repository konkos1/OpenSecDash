import sys
from pathlib import Path

from logging.config import fileConfig

from sqlalchemy import create_engine
from sqlalchemy import pool

from alembic import context

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)

# Our models
from app.core.settings import settings
from app.database.base import Base
from app.models import *  # noqa: F401,F403 - register all models for Alembic metadata

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
# target_metadata = None
target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", settings.database_url)

# These columns were introduced during the prototype-to-Alembic transition and
# may be nullable or non-nullable depending on which early schema created the
# SQLite database. Runtime code supplies defaults before insert, so the exact
# nullable flag is not a meaningful migration signal. Ignoring these columns in
# autogenerate keeps `alembic check` stable for both fresh and upgraded DBs.
NULLABILITY_COMPAT_COLUMNS = {
    ("assets", "type"),
    ("assets", "enabled"),
    ("assets", "update_check_type"),
    ("events", "created_at"),
    ("events", "event_time"),
}


def include_object(object_, name, type_, reflected, compare_to):
    if type_ == "column" and hasattr(object_, "table"):
        if (object_.table.name, name) in NULLABILITY_COMPAT_COLUMNS:
            return False
    return True

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    url = config.get_main_option("sqlalchemy.url")
    connect_args = {"timeout": 10, "check_same_thread": False} if url.startswith("sqlite") else {}
    connectable = create_engine(url, poolclass=pool.NullPool, connect_args=connect_args)

    with connectable.connect() as connection:
        if url.startswith("sqlite"):
            # Fail fast on locked SQLite databases instead of appearing to hang
            # indefinitely during app startup migrations. Commit immediately:
            # with SQLAlchemy 2.x even PRAGMA can open an implicit transaction;
            # if left open, Alembic's version-table update may be rolled back
            # on connection close while SQLite DDL has already persisted.
            connection.exec_driver_sql("PRAGMA busy_timeout = 10000")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
