"""add external identities and session auth method

Revision ID: b1c2d3e4f5a6
Revises: a3b4c5d6e7f8
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Downgrade restores a NOT NULL password hash. Passwordless OIDC-only accounts
# receive this marker instead of a generated password: it never parses as a
# scrypt hash, so verify_password() always rejects it.
DISABLED_PASSWORD_MARKER = "disabled$no-local-password"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    if "external_identities" not in _tables():
        op.create_table(
            "external_identities",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=20), nullable=False, server_default="oidc"),
            sa.Column("issuer", sa.String(length=512), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "provider",
                "issuer",
                "subject",
                name="uq_external_identity_provider_subject",
            ),
            sa.UniqueConstraint("provider", "user_id", name="uq_external_identity_provider_user"),
        )
        op.create_index("ix_external_identities_user_id", "external_identities", ["user_id"])

    if "auth_method" not in _columns("user_sessions"):
        op.add_column(
            "user_sessions",
            sa.Column("auth_method", sa.String(length=20), nullable=True, server_default="password"),
        )
        op.execute(sa.text("UPDATE user_sessions SET auth_method = 'password' WHERE auth_method IS NULL"))
        with op.batch_alter_table("user_sessions") as batch_op:
            batch_op.alter_column(
                "auth_method",
                existing_type=sa.String(length=20),
                existing_server_default="password",
                nullable=False,
            )

    if "users" in _tables():
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("password_hash", existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    if "users" in _tables():
        op.execute(
            sa.text("UPDATE users SET password_hash = :marker WHERE password_hash IS NULL").bindparams(
                marker=DISABLED_PASSWORD_MARKER
            )
        )
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("password_hash", existing_type=sa.String(length=255), nullable=False)

    if "auth_method" in _columns("user_sessions"):
        with op.batch_alter_table("user_sessions") as batch_op:
            batch_op.drop_column("auth_method")

    if "external_identities" in _tables():
        op.drop_index("ix_external_identities_user_id", table_name="external_identities")
        op.drop_table("external_identities")
