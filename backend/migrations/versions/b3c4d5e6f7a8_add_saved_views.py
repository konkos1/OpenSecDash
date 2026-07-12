"""add saved views

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("filter_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "name", name="uq_saved_view_scope_name"),
    )
    op.create_index("ix_saved_views_scope", "saved_views", ["scope"])


def downgrade() -> None:
    op.drop_table("saved_views")
