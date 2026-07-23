"""move asset update settings

Revision ID: de45fa67bc89
Revises: cd34ef56ab78
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "de45fa67bc89"
down_revision: Union[str, Sequence[str], None] = "cd34ef56ab78"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

settings = sa.table(
    "settings",
    sa.column("key", sa.String),
    sa.column("value", sa.String),
)


def _get(connection: sa.Connection, key: str) -> str | None:
    row = connection.execute(sa.select(settings.c.value).where(settings.c.key == key)).first()
    return str(row[0]) if row is not None and row[0] is not None else None


def _set_if_missing(connection: sa.Connection, key: str, value: str) -> None:
    existing = _get(connection, key)
    if existing is not None and existing != "":
        return
    if existing is None:
        connection.execute(settings.insert().values(key=key, value=value))
    else:
        connection.execute(settings.update().where(settings.c.key == key).values(value=value))


def upgrade() -> None:
    """Move the asset update settings of an installation that already has settings.

    A brand-new database is skipped on purpose: it has nothing to move and gets
    the same defaults from the startup seed moments later. An empty settings
    table is what identifies a new installation there (see
    app/database/init_db.py::seed_defaults), so writing here would make every
    new installation look like an upgrade.
    """
    connection = op.get_bind()
    if connection.execute(sa.select(settings.c.key).limit(1)).first() is None:
        return
    for new_key, old_keys, default in [
        ("asset_updates.github_token", ["plugin.json_assets.github_token", "plugin.assets.github_token", "github_token"], ""),
        ("asset_updates.github_interval", ["plugin.json_assets.github_interval"], "21600"),
    ]:
        value = next((legacy for old_key in old_keys if (legacy := _get(connection, old_key))), default)
        _set_if_missing(connection, new_key, value)

    connection.execute(settings.delete().where(settings.c.key.in_(["plugin.json_assets.github_token", "plugin.json_assets.github_interval", "plugin.assets.github_token"])))


def downgrade() -> None:
    connection = op.get_bind()
    token = _get(connection, "asset_updates.github_token") or ""
    interval = _get(connection, "asset_updates.github_interval") or "21600"
    _set_if_missing(connection, "plugin.json_assets.github_token", token)
    _set_if_missing(connection, "plugin.json_assets.github_interval", interval)
    connection.execute(settings.delete().where(settings.c.key.in_(["asset_updates.github_token", "asset_updates.github_interval"])))
