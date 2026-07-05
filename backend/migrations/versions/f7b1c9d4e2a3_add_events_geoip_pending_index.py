"""add partial index for the geoip backfill poll

Revision ID: f7b1c9d4e2a3
Revises: e95a3b2352c0
Create Date: 2026-07-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7b1c9d4e2a3"
down_revision: Union[str, Sequence[str], None] = "e95a3b2352c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {name for index in inspector.get_indexes(table_name) if (name := index["name"]) is not None}


def upgrade() -> None:
    if "ix_events_geoip_pending" not in _index_names("events"):
        op.create_index(
            "ix_events_geoip_pending",
            "events",
            ["geoip_checked"],
            sqlite_where=sa.text("geoip_checked = 0"),
        )


def downgrade() -> None:
    if "ix_events_geoip_pending" in _index_names("events"):
        op.drop_index("ix_events_geoip_pending", table_name="events")
