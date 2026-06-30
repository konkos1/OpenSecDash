"""add crowdsec decisions

Revision ID: bc23de45fa67
Revises: ab12cd34ef56
Create Date: 2026-06-29 00:00:00.000000

"""
from typing import Sequence, Union

import logging

from alembic import op
import sqlalchemy as sa

logger = logging.getLogger(__name__)


revision: str = "bc23de45fa67"
down_revision: Union[str, Sequence[str], None] = "ab12cd34ef56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, existing_columns: set[str], column: sa.Column) -> None:
    if column.name in existing_columns:
        return
    logger.info("Adding column %s.%s", table_name, column.name)
    op.add_column(table_name, column)
    existing_columns.add(str(column.name))


def upgrade() -> None:
    # Keep this migration idempotent: if a previous startup was interrupted
    # after creating the table but before Alembic stamped the version, a retry
    # should finish instead of failing or blocking on duplicate DDL.
    if "crowdsec_decisions" not in _tables():
        logger.info("Creating crowdsec_decisions table")
        op.create_table(
            "crowdsec_decisions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("decision_id", sa.String(length=100), nullable=False),
            sa.Column("ip", sa.String(length=128), nullable=False),
            sa.Column("scope", sa.String(length=50), nullable=True),
            sa.Column("decision_type", sa.String(length=50), nullable=False),
            sa.Column("origin", sa.String(length=100), nullable=True),
            sa.Column("scenario", sa.String(length=255), nullable=True),
            sa.Column("reason", sa.String(length=255), nullable=True),
            sa.Column("duration", sa.String(length=100), nullable=True),
            sa.Column("until", sa.DateTime(), nullable=True),
            sa.Column("raw_json", sa.JSON(), nullable=True),
            sa.Column("synced_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("decision_id", name="uq_crowdsec_decision_id"),
        )
    else:
        logger.info("crowdsec_decisions table already exists; continuing")

    columns = _columns("crowdsec_decisions")
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("decision_id", sa.String(length=100), nullable=False, server_default=""))
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("ip", sa.String(length=128), nullable=False, server_default=""))
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("scope", sa.String(length=50), nullable=True))
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("decision_type", sa.String(length=50), nullable=False, server_default="ban"))
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("origin", sa.String(length=100), nullable=True))
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("scenario", sa.String(length=255), nullable=True))
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("reason", sa.String(length=255), nullable=True))
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("duration", sa.String(length=100), nullable=True))
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("until", sa.DateTime(), nullable=True))
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("raw_json", sa.JSON(), nullable=True))
    _add_column_if_missing("crowdsec_decisions", columns, sa.Column("synced_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))

    indexes = _indexes("crowdsec_decisions")
    for name, columns in {
        "ix_crowdsec_decisions_decision_id": ["decision_id"],
        "ix_crowdsec_decisions_decision_type": ["decision_type"],
        "ix_crowdsec_decisions_ip": ["ip"],
        "ix_crowdsec_decisions_synced_at": ["synced_at"],
        "ix_crowdsec_decisions_until": ["until"],
    }.items():
        if name not in indexes:
            logger.info("Creating index %s", name)
            op.create_index(name, "crowdsec_decisions", columns, unique=False)
        else:
            logger.info("Index %s already exists; continuing", name)


def downgrade() -> None:
    if "crowdsec_decisions" not in _tables():
        return
    indexes = _indexes("crowdsec_decisions")
    for name in [
        "ix_crowdsec_decisions_until",
        "ix_crowdsec_decisions_synced_at",
        "ix_crowdsec_decisions_ip",
        "ix_crowdsec_decisions_decision_type",
        "ix_crowdsec_decisions_decision_id",
    ]:
        if name in indexes:
            op.drop_index(name, table_name="crowdsec_decisions")
    op.drop_table("crowdsec_decisions")
