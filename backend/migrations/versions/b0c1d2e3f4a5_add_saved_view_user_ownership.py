"""add saved view user ownership

Revision ID: b0c1d2e3f4a5
Revises: a9b8c7d6e5f4
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("saved_views")}
    if "user_id" in columns:
        return
    with op.batch_alter_table("saved_views") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.drop_constraint("uq_saved_view_scope_name", type_="unique")
        batch_op.create_unique_constraint("uq_saved_view_user_scope_name", ["user_id", "scope", "name"])
        batch_op.create_index("ix_saved_views_user_id", ["user_id"])


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("saved_views")}
    if "user_id" not in columns:
        return
    with op.batch_alter_table("saved_views") as batch_op:
        batch_op.drop_index("ix_saved_views_user_id")
        batch_op.drop_constraint("uq_saved_view_user_scope_name", type_="unique")
        batch_op.create_unique_constraint("uq_saved_view_scope_name", ["scope", "name"])
        batch_op.drop_column("user_id")
