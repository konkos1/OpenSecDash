"""add asn to geoip cache

Revision ID: c1d2e3f4a5b6
Revises: b9d0e1f2a3b4
Create Date: 2026-06-24 12:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if "asn" not in _columns("geoip_cache"):
        op.add_column("geoip_cache", sa.Column("asn", sa.String(length=32), nullable=True))
    if "ix_geoip_cache_asn" not in _indexes("geoip_cache"):
        op.create_index("ix_geoip_cache_asn", "geoip_cache", ["asn"], unique=False)


def downgrade() -> None:
    if "ix_geoip_cache_asn" in _indexes("geoip_cache"):
        op.drop_index("ix_geoip_cache_asn", table_name="geoip_cache")
    if "asn" in _columns("geoip_cache"):
        op.drop_column("geoip_cache", "asn")
