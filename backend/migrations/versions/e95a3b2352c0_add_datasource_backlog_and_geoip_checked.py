"""add datasource backlog tracking and event geoip checked flag

Revision ID: e95a3b2352c0
Revises: ef56ab78cd90
Create Date: 2026-07-05 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e95a3b2352c0"
down_revision: Union[str, Sequence[str], None] = "ef56ab78cd90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "backlog_pending" not in _columns("datasources"):
        op.add_column("datasources", sa.Column("backlog_pending", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "backlog_progress_percent" not in _columns("datasources"):
        op.add_column("datasources", sa.Column("backlog_progress_percent", sa.Integer(), nullable=True))
    if "geoip_checked" not in _columns("events"):
        op.add_column("events", sa.Column("geoip_checked", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    if "geoip_checked" in _columns("events"):
        op.drop_column("events", "geoip_checked")
    if "backlog_progress_percent" in _columns("datasources"):
        op.drop_column("datasources", "backlog_progress_percent")
    if "backlog_pending" in _columns("datasources"):
        op.drop_column("datasources", "backlog_pending")
