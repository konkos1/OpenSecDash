"""add precomputed events.is_local_ip and backfill it

Revision ID: a1e8c4d92b57
Revises: f7b1c9d4e2a3
Create Date: 2026-07-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1e8c4d92b57"
down_revision: Union[str, Sequence[str], None] = "f7b1c9d4e2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "is_local_ip" in _columns("events"):
        return
    op.add_column("events", sa.Column("is_local_ip", sa.Boolean(), nullable=False, server_default=sa.false()))

    # Backfill via DISTINCT ip: the classification needs Python's ipaddress
    # module, but there are far fewer distinct IPs than event rows, so one
    # UPDATE per local IP beats touching every row individually.
    from app.services.events import is_local_ip_value

    connection = op.get_bind()
    distinct_ips = [
        row[0]
        for row in connection.execute(sa.text("SELECT DISTINCT ip FROM events WHERE ip IS NOT NULL AND ip != ''"))
    ]
    local_ips = [ip for ip in distinct_ips if is_local_ip_value(ip)]
    for ip in local_ips:
        connection.execute(sa.text("UPDATE events SET is_local_ip = 1 WHERE ip = :ip"), {"ip": ip})


def downgrade() -> None:
    if "is_local_ip" in _columns("events"):
        op.drop_column("events", "is_local_ip")
