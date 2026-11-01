"""add insight rule schema two

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-11-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("insight_rules")}
    if "kind" not in columns:
        op.add_column("insight_rules", sa.Column("kind", sa.String(30), nullable=False, server_default="web_probe"))
    if "match_options" not in columns:
        op.add_column("insight_rules", sa.Column("match_options", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("insight_rules", "match_options")
    op.drop_column("insight_rules", "kind")
