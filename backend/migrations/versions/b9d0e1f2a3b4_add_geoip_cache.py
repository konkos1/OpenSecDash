"""add geoip cache

Revision ID: b9d0e1f2a3b4
Revises: a8c3d4e5f6a7
Create Date: 2026-06-21 23:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "a8c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if "geoip_cache" not in _tables():
        op.create_table(
            "geoip_cache",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("lookup_key", sa.String(length=128), nullable=False),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("country", sa.String(length=2), nullable=True),
            sa.Column("looked_up_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("last_error_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    indexes = _indexes("geoip_cache")
    if "ix_geoip_cache_lookup_key" not in indexes:
        op.create_index("ix_geoip_cache_lookup_key", "geoip_cache", ["lookup_key"], unique=True)
    for name, columns in {
        "ix_geoip_cache_provider": ["provider"],
        "ix_geoip_cache_country": ["country"],
        "ix_geoip_cache_looked_up_at": ["looked_up_at"],
        "ix_geoip_cache_expires_at": ["expires_at"],
    }.items():
        if name not in indexes:
            op.create_index(name, "geoip_cache", columns, unique=False)


def downgrade() -> None:
    if "geoip_cache" in _tables():
        op.drop_table("geoip_cache")
