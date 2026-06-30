"""add city to events and geoip cache

Revision ID: ab12cd34ef56
Revises: f3a4b5c6d7e8
Create Date: 2026-06-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ab12cd34ef56"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if "city" not in _columns("events"):
        op.add_column("events", sa.Column("city", sa.String(length=255), nullable=True))
    if "ix_events_city" not in _indexes("events"):
        op.create_index("ix_events_city", "events", ["city"], unique=False)

    if "city" not in _columns("geoip_cache"):
        op.add_column("geoip_cache", sa.Column("city", sa.String(length=255), nullable=True))
    if "ix_geoip_cache_city" not in _indexes("geoip_cache"):
        op.create_index("ix_geoip_cache_city", "geoip_cache", ["city"], unique=False)


def downgrade() -> None:
    if "ix_geoip_cache_city" in _indexes("geoip_cache"):
        op.drop_index("ix_geoip_cache_city", table_name="geoip_cache")
    if "city" in _columns("geoip_cache"):
        op.drop_column("geoip_cache", "city")

    if "ix_events_city" in _indexes("events"):
        op.drop_index("ix_events_city", table_name="events")
    if "city" in _columns("events"):
        op.drop_column("events", "city")
