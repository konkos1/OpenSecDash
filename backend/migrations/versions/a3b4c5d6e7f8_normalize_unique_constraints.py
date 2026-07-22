"""normalize unique constraints

Revision ID: a3b4c5d6e7f8
Revises: f1a2b3c4d5e6
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UNIQUE_INDEXES = (
    ("api_tokens", "ix_api_tokens_token_hash", "token_hash"),
    ("instance_files", "ix_instance_files_kind", "kind"),
    ("user_preferences", "ix_user_preferences_user_id", "user_id"),
    ("user_sessions", "ix_user_sessions_token_hash", "token_hash"),
    ("users", "ix_users_username", "username"),
)
SOURCE_CONSTRAINTS = (
    ("assets", "uq_asset_source_external"),
    ("systems", "uq_system_source_external"),
)


def _duplicate_source_group_count(table_name: str) -> int:
    table = sa.table(
        table_name,
        sa.column("source_plugin", sa.String()),
        sa.column("external_id", sa.String()),
    )
    duplicate_groups = (
        sa.select(sa.literal(1))
        .select_from(table)
        .where(table.c.source_plugin.is_not(None), table.c.external_id.is_not(None))
        .group_by(table.c.source_plugin, table.c.external_id)
        .having(sa.func.count() > 1)
        .subquery()
    )
    return int(op.get_bind().scalar(sa.select(sa.func.count()).select_from(duplicate_groups)) or 0)


def _ensure_source_ids_are_unique() -> None:
    duplicates = [
        f"{table_name}: {count} duplicate group(s)"
        for table_name, _constraint_name in SOURCE_CONSTRAINTS
        if (count := _duplicate_source_group_count(table_name))
    ]
    if duplicates:
        raise RuntimeError(
            "Cannot add source identity constraints; resolve duplicate "
            "(source_plugin, external_id) values first (" + "; ".join(duplicates) + ")."
        )


def _index_unique_state(table_name: str, index_name: str) -> bool | None:
    for index in sa.inspect(op.get_bind()).get_indexes(table_name):
        if index["name"] == index_name:
            return bool(index["unique"])
    return None


def _recreate_indexes(*, unique: bool) -> None:
    for table_name, index_name, column_name in UNIQUE_INDEXES:
        current_state = _index_unique_state(table_name, index_name)
        if current_state is unique:
            continue
        if current_state is not None:
            op.drop_index(index_name, table_name=table_name)
        op.create_index(index_name, table_name, [column_name], unique=unique)


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    constraints = sa.inspect(op.get_bind()).get_unique_constraints(table_name)
    return any(constraint["name"] == constraint_name for constraint in constraints)


def upgrade() -> None:
    # SQLite DDL is not transactional, so reject incompatible rows before any
    # index or table is changed and leave the existing schema retryable.
    _ensure_source_ids_are_unique()
    _recreate_indexes(unique=True)
    for table_name, constraint_name in SOURCE_CONSTRAINTS:
        if _has_unique_constraint(table_name, constraint_name):
            continue
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.create_unique_constraint(
                constraint_name,
                ["source_plugin", "external_id"],
            )


def downgrade() -> None:
    for table_name, constraint_name in reversed(SOURCE_CONSTRAINTS):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(constraint_name, type_="unique")
    _recreate_indexes(unique=False)
