"""add isp to events and geoip cache

Revision ID: f3a4b5c6d7e8
Revises: c1d2e3f4a5b6
Create Date: 2026-06-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if "isp" not in _columns("events"):
        op.add_column("events", sa.Column("isp", sa.String(length=255), nullable=True))
    if "ix_events_isp" not in _indexes("events"):
        op.create_index("ix_events_isp", "events", ["isp"], unique=False)

    if "isp" not in _columns("geoip_cache"):
        op.add_column("geoip_cache", sa.Column("isp", sa.String(length=255), nullable=True))
    if "ix_geoip_cache_isp" not in _indexes("geoip_cache"):
        op.create_index("ix_geoip_cache_isp", "geoip_cache", ["isp"], unique=False)


def downgrade() -> None:
    if "ix_geoip_cache_isp" in _indexes("geoip_cache"):
        op.drop_index("ix_geoip_cache_isp", table_name="geoip_cache")
    if "isp" in _columns("geoip_cache"):
        op.drop_column("geoip_cache", "isp")

    if "ix_events_isp" in _indexes("events"):
        op.drop_index("ix_events_isp", table_name="events")
    if "isp" in _columns("events"):
        op.drop_column("events", "isp")
