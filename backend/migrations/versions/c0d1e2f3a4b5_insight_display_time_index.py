"""Index finding time with legacy creation-time fallback."""
from alembic import op
import sqlalchemy as sa

revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_insights_display_time", "insights", [sa.text("coalesce(occurred_at, timestamp)")], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_insights_display_time", table_name="insights")
