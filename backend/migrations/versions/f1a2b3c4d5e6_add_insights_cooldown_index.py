"""add insights cooldown index

Revision ID: f1a2b3c4d5e6
Revises: e6f7a8b9c0d1
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_insights_type_ip_timestamp",
        "insights",
        ["type", "ip", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_insights_type_ip_timestamp", table_name="insights")
