"""add notification models

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="event"),
        sa.Column("match_types", sa.JSON(), nullable=False),
        sa.Column("min_severity", sa.String(length=20), nullable=False, server_default="info"),
        sa.Column("countries", sa.JSON(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=50), nullable=False, server_default="email"),
        sa.Column("min_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("window_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", name="uq_notification_rule_id"),
    )
    op.create_index("ix_notification_rules_rule_id", "notification_rules", ["rule_id"])
    op.create_index("ix_notification_rules_enabled", "notification_rules", ["enabled"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("rule_id", sa.String(length=120), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False, server_default="email"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index("ix_notifications_rule_id", "notifications", ["rule_id"])
    op.create_index("ix_notifications_status", "notifications", ["status"])
    op.create_index("ix_notifications_rule_created", "notifications", ["rule_id", "created_at"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("notification_rules")
