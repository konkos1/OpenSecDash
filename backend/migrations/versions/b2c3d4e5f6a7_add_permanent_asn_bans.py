"""add permanent ASN ban models

Revision ID: b2c3d4e5f6a7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = {
    "crowdsec_asn_bans",
    "crowdsec_asn_ban_exceptions",
    "crowdsec_asn_ban_enforcements",
}


def upgrade() -> None:
    existing_tables = TABLES.intersection(sa.inspect(op.get_bind()).get_table_names())
    if existing_tables == TABLES:
        return
    if existing_tables:
        tables = ", ".join(sorted(existing_tables))
        raise RuntimeError(f"Permanent ASN ban schema is incomplete; existing tables: {tables}")

    op.create_table(
        "crowdsec_asn_bans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asn", sa.String(length=32), nullable=False),
        sa.Column("provider_name", sa.String(length=255), nullable=True),
        sa.Column("previous_provider_name", sa.String(length=255), nullable=True),
        sa.Column("provider_review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider_name_changed_at", sa.DateTime(), nullable=True),
        sa.Column("provider_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_matched_at", sa.DateTime(), nullable=True),
        sa.Column("removal_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'removing')", name="ck_crowdsec_asn_ban_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asn", name="uq_crowdsec_asn_ban_asn"),
    )
    op.create_index("ix_crowdsec_asn_bans_asn", "crowdsec_asn_bans", ["asn"])
    op.create_index(
        "ix_crowdsec_asn_bans_provider_review_required",
        "crowdsec_asn_bans",
        ["provider_review_required"],
    )
    op.create_index(
        "ix_crowdsec_asn_bans_provider_name_changed_at",
        "crowdsec_asn_bans",
        ["provider_name_changed_at"],
    )
    op.create_index("ix_crowdsec_asn_bans_status", "crowdsec_asn_bans", ["status"])
    op.create_index("ix_crowdsec_asn_bans_created_at", "crowdsec_asn_bans", ["created_at"])
    op.create_index("ix_crowdsec_asn_bans_last_matched_at", "crowdsec_asn_bans", ["last_matched_at"])

    op.create_table(
        "crowdsec_asn_ban_exceptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asn_ban_id", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_action_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["asn_ban_id"], ["crowdsec_asn_bans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asn_ban_id", "ip", name="uq_crowdsec_asn_ban_exception_ip"),
    )
    op.create_index(
        "ix_crowdsec_asn_ban_exceptions_asn_ban_id",
        "crowdsec_asn_ban_exceptions",
        ["asn_ban_id"],
    )
    op.create_index("ix_crowdsec_asn_ban_exceptions_ip", "crowdsec_asn_ban_exceptions", ["ip"])
    op.create_index(
        "ix_crowdsec_asn_ban_exceptions_created_at",
        "crowdsec_asn_ban_exceptions",
        ["created_at"],
    )

    op.create_table(
        "crowdsec_asn_ban_enforcements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asn_ban_id", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("last_event_id", sa.Integer(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("decision_id", sa.String(length=100), nullable=True),
        sa.Column("decision_until", sa.DateTime(), nullable=True),
        sa.Column("scenario", sa.String(length=255), nullable=True),
        sa.Column("last_observed_asn", sa.String(length=32), nullable=True),
        sa.Column("release_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("release_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["asn_ban_id"], ["crowdsec_asn_bans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asn_ban_id", "ip", name="uq_crowdsec_asn_ban_enforcement_ip"),
    )
    op.create_index(
        "ix_crowdsec_asn_ban_enforcements_asn_ban_id",
        "crowdsec_asn_ban_enforcements",
        ["asn_ban_id"],
    )
    op.create_index("ix_crowdsec_asn_ban_enforcements_ip", "crowdsec_asn_ban_enforcements", ["ip"])
    op.create_index(
        "ix_crowdsec_asn_ban_enforcements_last_seen_at",
        "crowdsec_asn_ban_enforcements",
        ["last_seen_at"],
    )
    op.create_index(
        "ix_crowdsec_asn_ban_enforcements_decision_id",
        "crowdsec_asn_ban_enforcements",
        ["decision_id"],
    )
    op.create_index(
        "ix_crowdsec_asn_ban_enforcements_decision_until",
        "crowdsec_asn_ban_enforcements",
        ["decision_until"],
    )
    op.create_index(
        "ix_crowdsec_asn_ban_enforcements_last_observed_asn",
        "crowdsec_asn_ban_enforcements",
        ["last_observed_asn"],
    )
    op.create_index(
        "ix_crowdsec_asn_ban_enforcements_release_pending",
        "crowdsec_asn_ban_enforcements",
        ["release_pending"],
    )


def downgrade() -> None:
    op.drop_table("crowdsec_asn_ban_enforcements")
    op.drop_table("crowdsec_asn_ban_exceptions")
    op.drop_table("crowdsec_asn_bans")
