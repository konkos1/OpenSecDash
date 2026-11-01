"""Index Level 2 anchors by event type and creation window."""
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_events_type_created", "events", ["event_type", "created_at"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_events_type_created", table_name="events")
