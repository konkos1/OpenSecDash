"""add user preferences

Revision ID: c2d3e4f5a6b7
Revises: b0c1d2e3f4a5
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEFAULTS = {
    "language": "en",
    "live_default": "true",
    "theme": "auto",
    "accent_color": "blue",
    "live_page_refresh": "true",
}
_GLOBAL_SETTING_KEYS = {
    "language": "language",
    "live_default": "live_default",
    "theme": "theme",
    "accent_color": "instance_accent_color",
    "live_page_refresh": "live_page_refresh",
}
_ALLOWED_VALUES = {
    "language": {"de", "en"},
    "live_default": {"true", "false"},
    "theme": {"auto", "dark", "light"},
    "accent_color": {"blue", "green", "orange", "red"},
    "live_page_refresh": {"true", "false"},
}


def _preferences_from_settings(settings: dict[str, str]) -> dict[str, str]:
    return {
        preference_key: value
        if (value := settings.get(setting_key, _DEFAULTS[preference_key])) in _ALLOWED_VALUES[preference_key]
        else _DEFAULTS[preference_key]
        for preference_key, setting_key in _GLOBAL_SETTING_KEYS.items()
    }


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("live_default", sa.String(length=5), nullable=False),
        sa.Column("theme", sa.String(length=5), nullable=False),
        sa.Column("accent_color", sa.String(length=6), nullable=False),
        sa.Column("live_page_refresh", sa.String(length=5), nullable=False),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"])

    bind = op.get_bind()
    settings_table = sa.table("settings", sa.column("key", sa.String()), sa.column("value", sa.String()))
    stored_settings = {key: value for key, value in bind.execute(sa.select(settings_table.c.key, settings_table.c.value))}
    users_table = sa.table("users", sa.column("id", sa.Integer()))
    preferences_table = sa.table(
        "user_preferences",
        sa.column("user_id", sa.Integer()),
        sa.column("language", sa.String()),
        sa.column("live_default", sa.String()),
        sa.column("theme", sa.String()),
        sa.column("accent_color", sa.String()),
        sa.column("live_page_refresh", sa.String()),
    )
    preferences = _preferences_from_settings(stored_settings)
    for (user_id,) in bind.execute(sa.select(users_table.c.id)):
        bind.execute(preferences_table.insert().values(user_id=user_id, **preferences))


def downgrade() -> None:
    op.drop_table("user_preferences")
