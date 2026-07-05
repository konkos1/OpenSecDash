"""add composite index for the per-insert dedupe lookup

Revision ID: b2f9d5e83c61
Revises: a1e8c4d92b57
Create Date: 2026-07-05 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2f9d5e83c61"
down_revision: Union[str, Sequence[str], None] = "a1e8c4d92b57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {name for index in inspector.get_indexes(table_name) if (name := index["name"]) is not None}


def upgrade() -> None:
    if "ix_events_dedupe_raw" not in _index_names("events"):
        op.create_index("ix_events_dedupe_raw", "events", ["plugin", "event_type", "raw_data"])


def downgrade() -> None:
    if "ix_events_dedupe_raw" in _index_names("events"):
        op.drop_index("ix_events_dedupe_raw", table_name="events")
