"""add saved view query state

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("saved_views", sa.Column("query_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("saved_views", "query_json")
