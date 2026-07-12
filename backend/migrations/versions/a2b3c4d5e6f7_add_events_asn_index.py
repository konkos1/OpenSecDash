"""add events ASN index

Revision ID: a2b3c4d5e6f7
Revises: f6a7b8c9d0e1
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_events_asn", "events", ["asn"])


def downgrade() -> None:
    op.drop_index("ix_events_asn", table_name="events")
