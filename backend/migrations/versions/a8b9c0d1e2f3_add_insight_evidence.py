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
    # SQLite DDL can outlive a failed migration unless a real transaction exists.
    with op.get_bind().begin_nested():
        _upgrade()


def _upgrade() -> None:
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
        if inspector.has_table("_alembic_tmp_insights"):
            metadata = sa.MetaData()
            insights = sa.Table("insights", metadata, autoload_with=op.get_bind())
            temporary = sa.Table("_alembic_tmp_insights", metadata, autoload_with=op.get_bind())
            # Only discard a failed batch copy when every row survives in the source.
            if set(temporary.c.keys()) != set(insights.c.keys()):
                raise RuntimeError("Cannot safely recover interrupted insights migration: column mismatch")
            missing = sa.select(*temporary.c).except_(
                sa.select(*(insights.c[column.name] for column in temporary.c))
            ).limit(1)
            if op.get_bind().scalar(sa.select(missing.exists())):
                raise RuntimeError("Cannot safely recover interrupted insights migration: unpreserved rows")
            op.drop_table("_alembic_tmp_insights")
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
