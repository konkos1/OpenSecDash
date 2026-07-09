"""add dashboard hour rollups

Revision ID: d4e5f6a7b8c9
Revises: c8d9e0f1a2b3
Create Date: 2026-07-09 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return

    # Rebuild only the dashboard hour metrics. Existing summary/country/scenario
    # rollups stay untouched. The metrics are UTC-hour buckets and are used only
    # when the dashboard's UI day matches the UTC daily rollup boundary.
    op.execute(sa.text("DELETE FROM aggregations_daily WHERE metric IN ('hour_access', 'hour_security')"))
    op.execute(
        sa.text(
            """
            INSERT INTO aggregations_daily(date, metric, key, value)
            SELECT strftime('%Y-%m-%d', event_time), 'hour_access', strftime('%H', event_time), count(*)
            FROM events
            WHERE event_time IS NOT NULL AND event_type LIKE 'access.%'
            GROUP BY strftime('%Y-%m-%d', event_time), strftime('%H', event_time)
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO aggregations_daily(date, metric, key, value)
            SELECT strftime('%Y-%m-%d', event_time), 'hour_security', strftime('%H', event_time), count(*)
            FROM events
            WHERE event_time IS NOT NULL AND event_type LIKE 'security.%'
            GROUP BY strftime('%Y-%m-%d', event_time), strftime('%H', event_time)
            """
        )
    )
    op.execute(sa.text("ANALYZE"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(sa.text("DELETE FROM aggregations_daily WHERE metric IN ('hour_access', 'hour_security')"))
        op.execute(sa.text("ANALYZE"))
