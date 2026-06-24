"""add asset host url

Revision ID: a8c3d4e5f6a7
Revises: 7f4a4a9c2b1e
Create Date: 2026-06-21 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "7f4a4a9c2b1e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if "host_url" not in _columns("assets"):
        op.add_column("assets", sa.Column("host_url", sa.String(length=2048), nullable=True))
    if "ix_assets_host_url" not in _indexes("assets"):
        op.create_index("ix_assets_host_url", "assets", ["host_url"], unique=False)


def downgrade() -> None:
    if "ix_assets_host_url" in _indexes("assets"):
        op.drop_index("ix_assets_host_url", table_name="assets")
    if "host_url" in _columns("assets"):
        op.drop_column("assets", "host_url")
