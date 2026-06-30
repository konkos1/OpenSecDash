"""add asset source ids

Revision ID: cd34ef56ab78
Revises: bc23de45fa67
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cd34ef56ab78"
down_revision: Union[str, Sequence[str], None] = "bc23de45fa67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column("systems", sa.Column("source_plugin", sa.String(length=100), nullable=True))
    _add_column("systems", sa.Column("external_id", sa.String(length=255), nullable=True))
    _add_column("assets", sa.Column("source_plugin", sa.String(length=100), nullable=True))
    _add_column("assets", sa.Column("external_id", sa.String(length=255), nullable=True))

    for table_name in ["systems", "assets"]:
        indexes = _indexes(table_name)
        for index_name, columns in {
            f"ix_{table_name}_source_plugin": ["source_plugin"],
            f"ix_{table_name}_external_id": ["external_id"],
        }.items():
            if index_name not in indexes:
                op.create_index(index_name, table_name, columns, unique=False)


def downgrade() -> None:
    for table_name in ["assets", "systems"]:
        indexes = _indexes(table_name)
        for index_name in [f"ix_{table_name}_external_id", f"ix_{table_name}_source_plugin"]:
            if index_name in indexes:
                op.drop_index(index_name, table_name=table_name)
        columns = _columns(table_name)
        if "external_id" in columns:
            op.drop_column(table_name, "external_id")
        if "source_plugin" in columns:
            op.drop_column(table_name, "source_plugin")
