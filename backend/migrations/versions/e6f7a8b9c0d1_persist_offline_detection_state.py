"""persist offline detection state

Revision ID: e6f7a8b9c0d1
Revises: d3e4f5a6b7c8
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if "offline_event_for_last_seen" not in _columns("systems"):
        op.add_column(
            "systems",
            sa.Column("offline_event_for_last_seen", sa.DateTime(), nullable=True),
        )
    if "ix_systems_last_seen" not in _indexes("systems"):
        op.create_index(
            "ix_systems_last_seen",
            "systems",
            ["last_seen"],
            unique=False,
        )


def downgrade() -> None:
    if "ix_systems_last_seen" in _indexes("systems"):
        op.drop_index("ix_systems_last_seen", table_name="systems")
    if "offline_event_for_last_seen" in _columns("systems"):
        op.drop_column("systems", "offline_event_for_last_seen")
