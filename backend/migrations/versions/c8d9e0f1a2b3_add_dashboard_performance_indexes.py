"""add dashboard performance indexes

Revision ID: c8d9e0f1a2b3
Revises: b2f9d5e83c61
Create Date: 2026-07-09 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b2f9d5e83c61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DASHBOARD_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("ix_events_plugin_time", ["plugin", "event_time"]),
    ("ix_events_plugin_type_time", ["plugin", "event_type", "event_time"]),
    ("ix_events_plugin_local_time", ["plugin", "is_local_ip", "event_time"]),
    ("ix_events_country_time", ["country", "event_time"]),
    ("ix_events_plugin_country_time", ["plugin", "country", "event_time"]),
)


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {name for index in inspector.get_indexes(table_name) if (name := index["name"]) is not None}


def upgrade() -> None:
    existing = _index_names("events")
    for index_name, columns in DASHBOARD_INDEXES:
        if index_name not in existing:
            op.create_index(index_name, "events", columns)

    # Refresh SQLite statistics immediately after adding the composite indexes
    # so the query planner can prefer them for dashboard/event queries without
    # requiring a manual maintenance step after migration.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(sa.text("ANALYZE"))


def downgrade() -> None:
    existing = _index_names("events")
    for index_name, _columns in reversed(DASHBOARD_INDEXES):
        if index_name in existing:
            op.drop_index(index_name, table_name="events")

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(sa.text("ANALYZE"))
