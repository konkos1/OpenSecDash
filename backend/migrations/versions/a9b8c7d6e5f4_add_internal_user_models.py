"""add internal user models

Revision ID: a9b8c7d6e5f4
Revises: f8c1a2d3e4b5
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, Sequence[str], None] = "f8c1a2d3e4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "users" not in _tables():
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False, server_default="viewer"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("username"),
        )
        op.create_index("ix_users_username", "users", ["username"])

    if "user_sessions" not in _tables():
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("token_hash"),
        )
        op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"])
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
        op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    if "api_tokens" not in _tables():
        op.create_table(
            "api_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("scope", sa.String(length=20), nullable=False, server_default="read"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("token_hash"),
        )
        op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"])
        op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])


def downgrade() -> None:
    if "api_tokens" in _tables():
        op.drop_table("api_tokens")
    if "user_sessions" in _tables():
        op.drop_table("user_sessions")
    if "users" in _tables():
        op.drop_table("users")
