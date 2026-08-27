"""add events status time index

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        name
        for index in inspector.get_indexes("events")
        if (name := index["name"]) is not None
    }


def _analyze_sqlite() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(sa.text("ANALYZE"))


def upgrade() -> None:
    existing = _index_names()
    if "ix_events_status_time" not in existing:
        op.create_index("ix_events_status_time", "events", ["status_code", "event_time"])
    if "ix_events_status_code" in existing:
        op.drop_index("ix_events_status_code", table_name="events")
    _analyze_sqlite()


def downgrade() -> None:
    existing = _index_names()
    if "ix_events_status_code" not in existing:
        op.create_index("ix_events_status_code", "events", ["status_code"])
    if "ix_events_status_time" in existing:
        op.drop_index("ix_events_status_time", table_name="events")
    _analyze_sqlite()
