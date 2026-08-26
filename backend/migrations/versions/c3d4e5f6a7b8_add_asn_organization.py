"""add ASN organization and provider-change candidates

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if "asn_organization" not in _columns("events"):
        op.add_column("events", sa.Column("asn_organization", sa.String(length=255), nullable=True))
    if "ix_events_asn_organization" not in _indexes("events"):
        op.create_index("ix_events_asn_organization", "events", ["asn_organization"], unique=False)

    if "asn_organization" not in _columns("geoip_cache"):
        op.add_column("geoip_cache", sa.Column("asn_organization", sa.String(length=255), nullable=True))
    if "ix_geoip_cache_asn_organization" not in _indexes("geoip_cache"):
        op.create_index(
            "ix_geoip_cache_asn_organization",
            "geoip_cache",
            ["asn_organization"],
            unique=False,
        )

    policy_columns = _columns("crowdsec_asn_bans")
    if "provider_candidate_name" not in policy_columns:
        op.add_column(
            "crowdsec_asn_bans",
            sa.Column("provider_candidate_name", sa.String(length=255), nullable=True),
        )
    if "provider_candidate_first_ip" not in policy_columns:
        op.add_column(
            "crowdsec_asn_bans",
            sa.Column("provider_candidate_first_ip", sa.String(length=64), nullable=True),
        )
    if "provider_candidate_last_event_id" not in policy_columns:
        op.add_column(
            "crowdsec_asn_bans",
            sa.Column("provider_candidate_last_event_id", sa.Integer(), nullable=True),
        )
    if "provider_candidate_observations" not in policy_columns:
        op.add_column(
            "crowdsec_asn_bans",
            sa.Column(
                "provider_candidate_observations",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if "provider_candidate_distinct_ip_seen" not in policy_columns:
        op.add_column(
            "crowdsec_asn_bans",
            sa.Column(
                "provider_candidate_distinct_ip_seen",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    policy_columns = _columns("crowdsec_asn_bans")
    if "provider_candidate_distinct_ip_seen" in policy_columns:
        op.drop_column("crowdsec_asn_bans", "provider_candidate_distinct_ip_seen")
    if "provider_candidate_observations" in policy_columns:
        op.drop_column("crowdsec_asn_bans", "provider_candidate_observations")
    if "provider_candidate_last_event_id" in policy_columns:
        op.drop_column("crowdsec_asn_bans", "provider_candidate_last_event_id")
    if "provider_candidate_first_ip" in policy_columns:
        op.drop_column("crowdsec_asn_bans", "provider_candidate_first_ip")
    if "provider_candidate_name" in policy_columns:
        op.drop_column("crowdsec_asn_bans", "provider_candidate_name")

    if "ix_geoip_cache_asn_organization" in _indexes("geoip_cache"):
        op.drop_index("ix_geoip_cache_asn_organization", table_name="geoip_cache")
    if "asn_organization" in _columns("geoip_cache"):
        op.drop_column("geoip_cache", "asn_organization")

    if "ix_events_asn_organization" in _indexes("events"):
        op.drop_index("ix_events_asn_organization", table_name="events")
    if "asn_organization" in _columns("events"):
        op.drop_column("events", "asn_organization")
