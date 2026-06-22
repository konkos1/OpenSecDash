"""add asset mqtt publish flag

Revision ID: 7f4a4a9c2b1e
Revises: 0d5574d55732
Create Date: 2026-06-21 21:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f4a4a9c2b1e"
down_revision: Union[str, Sequence[str], None] = "0d5574d55732"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if "mqtt_publish_enabled" not in _columns("assets"):
        op.add_column(
            "assets",
            sa.Column(
                "mqtt_publish_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
    if "ix_assets_mqtt_publish_enabled" not in _indexes("assets"):
        op.create_index(
            "ix_assets_mqtt_publish_enabled",
            "assets",
            ["mqtt_publish_enabled"],
            unique=False,
        )


def downgrade() -> None:
    if "ix_assets_mqtt_publish_enabled" in _indexes("assets"):
        op.drop_index("ix_assets_mqtt_publish_enabled", table_name="assets")
    if "mqtt_publish_enabled" in _columns("assets"):
        op.drop_column("assets", "mqtt_publish_enabled")
