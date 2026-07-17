"""copy legacy views to existing users

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    users_table = sa.table("users", sa.column("id", sa.Integer()))
    saved_views_table = sa.table(
        "saved_views",
        sa.column("user_id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("scope", sa.String()),
        sa.column("filter_json", sa.JSON()),
        sa.column("query_json", sa.JSON()),
        sa.column("created_at", sa.DateTime()),
    )
    user_ids = [user_id for (user_id,) in bind.execute(sa.select(users_table.c.id))]
    legacy_views = bind.execute(
        sa.select(
            saved_views_table.c.name,
            saved_views_table.c.scope,
            saved_views_table.c.filter_json,
            saved_views_table.c.query_json,
            saved_views_table.c.created_at,
        ).where(saved_views_table.c.user_id.is_(None))
    ).all()
    existing_pairs = {
        (user_id, scope, name)
        for user_id, scope, name in bind.execute(
            sa.select(
                saved_views_table.c.user_id,
                saved_views_table.c.scope,
                saved_views_table.c.name,
            ).where(saved_views_table.c.user_id.is_not(None))
        )
    }

    for user_id in user_ids:
        for name, scope, filter_json, query_json, created_at in legacy_views:
            if (user_id, scope, name) in existing_pairs:
                continue
            bind.execute(
                saved_views_table.insert().values(
                    user_id=user_id,
                    name=name,
                    scope=scope,
                    filter_json=filter_json,
                    query_json=query_json,
                    created_at=created_at,
                )
            )


def downgrade() -> None:
    pass
