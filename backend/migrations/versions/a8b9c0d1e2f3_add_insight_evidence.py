"""add insight event time and evidence

Revision ID: a8b9c0d1e2f3
Revises: e7f8a9b0c1d2
Create Date: 2026-11-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("insights")}
    for column in (
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("correlation_key", sa.String(160), nullable=True),
        sa.Column("dedupe_key", sa.String(255), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
    ):
        if column.name not in columns:
            op.add_column("insights", column)
    indexes = {index["name"] for index in inspector.get_indexes("insights")}
    if "ix_insights_occurred_at" not in indexes:
        op.create_index("ix_insights_occurred_at", "insights", ["occurred_at"])
    if "ix_insights_type_correlation_occurred" not in indexes:
        op.create_index("ix_insights_type_correlation_occurred", "insights", ["type", "correlation_key", "occurred_at"])
    # Prototype installations may already have the current metadata schema.
    constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("insights")}
    if "uq_insights_dedupe_key" not in constraints:
        with op.batch_alter_table("insights") as batch_op:
            batch_op.create_unique_constraint("uq_insights_dedupe_key", ["dedupe_key"])


def downgrade() -> None:
    with op.batch_alter_table("insights") as batch_op:
        batch_op.drop_constraint("uq_insights_dedupe_key", type_="unique")
    op.drop_index("ix_insights_type_correlation_occurred", table_name="insights")
    op.drop_index("ix_insights_occurred_at", table_name="insights")
    op.drop_column("insights", "evidence")
    op.drop_column("insights", "dedupe_key")
    op.drop_column("insights", "correlation_key")
    op.drop_column("insights", "occurred_at")
