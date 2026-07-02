"""add insight rules

Revision ID: ef56ab78cd90
Revises: de45fa67bc89
Create Date: 2026-07-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ef56ab78cd90"
down_revision: Union[str, Sequence[str], None] = "de45fa67bc89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "insight_rules" not in _tables():
        op.create_table(
            "insight_rules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("rule_id", sa.String(length=120), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False, server_default="bundled"),
            sa.Column("schema_version", sa.String(length=20), nullable=False, server_default="1"),
            sa.Column("ruleset_version", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("level", sa.String(length=20), nullable=False, server_default="medium"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.7"),
            sa.Column("event_types", sa.JSON(), nullable=False),
            sa.Column("path_contains_any", sa.JSON(), nullable=False),
            sa.Column("group_by", sa.String(length=50), nullable=False, server_default="ip"),
            sa.Column("window_minutes", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("threshold", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("rule_id", name="uq_insight_rule_id"),
        )
        op.create_index("ix_insight_rules_rule_id", "insight_rules", ["rule_id"])
        op.create_index("ix_insight_rules_source", "insight_rules", ["source"])
        op.create_index("ix_insight_rules_is_active", "insight_rules", ["is_active"])
        op.create_index("ix_insight_rules_last_seen_at", "insight_rules", ["last_seen_at"])

    settings = sa.table("settings", sa.column("key", sa.String))
    op.get_bind().execute(settings.delete().where(settings.c.key == "insight_rules.cache_json"))


def downgrade() -> None:
    if "insight_rules" in _tables():
        for index_name in ["ix_insight_rules_last_seen_at", "ix_insight_rules_is_active", "ix_insight_rules_source", "ix_insight_rules_rule_id"]:
            try:
                op.drop_index(index_name, table_name="insight_rules")
            except Exception:
                pass
        op.drop_table("insight_rules")
