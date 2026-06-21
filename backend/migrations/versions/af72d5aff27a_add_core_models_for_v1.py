"""add core models for v1

Revision ID: af72d5aff27a
Revises: e2ae67a5ba6c
Create Date: 2026-06-21 17:05:40.215869

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "af72d5aff27a"
down_revision: Union[str, Sequence[str], None] = "e2ae67a5ba6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(
    index_name: str,
    table_name: str,
    columns: list[str],
    unique: bool = False,
) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if index_name in _indexes(table_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    """Upgrade schema.

    This migration intentionally avoids SQLite ``ALTER COLUMN ... SET NOT NULL``.
    Existing prototype databases can contain rows where v1 columns were created
    by app startup code but still contain NULL. SQLite validates those rows when
    Alembic tries to enforce NOT NULL and raises ``sqlite3.IntegrityError:
    constraint failed``. We therefore add/backfill columns in a SQLite-safe and
    idempotent way and let the ORM provide defaults for new writes.
    """
    bind = op.get_bind()
    tables = _table_names()

    if "plugins" not in tables:
        op.create_table(
            "plugins",
            sa.Column("id", sa.String(length=100), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("version", sa.String(length=50), nullable=False, server_default="1.0.0"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("author", sa.String(length=255), nullable=True),
            sa.Column("api_version", sa.String(length=20), nullable=False, server_default="1"),
            sa.Column("capabilities", sa.JSON(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="healthy"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "datasources" not in tables:
        op.create_table(
            "datasources",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("plugin_id", sa.String(length=100), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("source_type", sa.String(length=50), nullable=False, server_default="logfile"),
            sa.Column("config", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="disabled"),
            sa.Column("last_event_at", sa.DateTime(), nullable=True),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("events_processed", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "insights" not in tables:
        op.create_table(
            "insights",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("type", sa.String(length=100), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("level", sa.String(length=20), nullable=False, server_default="medium"),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("related_event_ids", sa.JSON(), nullable=True),
            sa.Column("ip", sa.String(length=64), nullable=True),
            sa.Column("asset_id", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "actions" not in tables:
        op.create_table(
            "actions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("action_type", sa.String(length=100), nullable=False),
            sa.Column("plugin_id", sa.String(length=100), nullable=False, server_default="core"),
            sa.Column("target_type", sa.String(length=50), nullable=False, server_default="ip"),
            sa.Column("target", sa.String(length=255), nullable=False),
            sa.Column("parameters", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.PrimaryKeyConstraint("id"),
        )

    if "aggregations_daily" not in tables:
        op.create_table(
            "aggregations_daily",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("date", sa.String(length=10), nullable=False),
            sa.Column("metric", sa.String(length=100), nullable=False),
            sa.Column("key", sa.String(length=255), nullable=False),
            sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "aggregations_monthly" not in tables:
        op.create_table(
            "aggregations_monthly",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("month", sa.String(length=7), nullable=False),
            sa.Column("metric", sa.String(length=100), nullable=False),
            sa.Column("key", sa.String(length=255), nullable=False),
            sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "diagnostics" not in tables:
        op.create_table(
            "diagnostics",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("plugin", sa.String(length=100), nullable=False),
            sa.Column("component", sa.String(length=100), nullable=False, server_default="plugin"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="healthy"),
            sa.Column("last_run", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    _add_column_if_missing("assets", sa.Column("type", sa.String(length=50), nullable=True, server_default="application"))
    _add_column_if_missing("assets", sa.Column("description", sa.Text(), nullable=True))
    _add_column_if_missing("assets", sa.Column("enabled", sa.Boolean(), nullable=True, server_default=sa.text("1")))
    _add_column_if_missing("assets", sa.Column("hostname", sa.String(length=255), nullable=True))
    _add_column_if_missing("assets", sa.Column("url", sa.String(length=2048), nullable=True))
    _add_column_if_missing("assets", sa.Column("icon", sa.String(length=255), nullable=True))
    _add_column_if_missing("assets", sa.Column("tags", sa.JSON(), nullable=True))
    _add_column_if_missing("assets", sa.Column("release_api_url", sa.String(length=2048), nullable=True))
    _add_column_if_missing("assets", sa.Column("release_web_url", sa.String(length=2048), nullable=True))
    _add_column_if_missing("assets", sa.Column("update_check_type", sa.String(length=50), nullable=True, server_default="github_release"))
    _add_column_if_missing("assets", sa.Column("last_checked", sa.DateTime(), nullable=True))

    bind.execute(sa.text("UPDATE assets SET type = 'application' WHERE type IS NULL"))
    bind.execute(sa.text("UPDATE assets SET enabled = 1 WHERE enabled IS NULL"))
    bind.execute(sa.text("UPDATE assets SET update_check_type = 'github_release' WHERE update_check_type IS NULL"))

    _add_column_if_missing("systems", sa.Column("last_seen", sa.DateTime(), nullable=True))

    _add_column_if_missing("events", sa.Column("created_at", sa.DateTime(), nullable=True))
    _add_column_if_missing("events", sa.Column("event_time", sa.DateTime(), nullable=True))
    _add_column_if_missing("events", sa.Column("source_id", sa.String(length=100), nullable=True))
    _add_column_if_missing("events", sa.Column("plugin_id", sa.String(length=100), nullable=True))
    _add_column_if_missing("events", sa.Column("asn", sa.String(length=32), nullable=True))
    _add_column_if_missing("events", sa.Column("asset_id", sa.Integer(), nullable=True))
    _add_column_if_missing("events", sa.Column("method", sa.String(length=16), nullable=True))
    _add_column_if_missing("events", sa.Column("raw_data", sa.Text(), nullable=True))
    _add_column_if_missing("events", sa.Column("retention_class", sa.String(length=20), nullable=True, server_default="raw"))

    bind.execute(sa.text("UPDATE events SET created_at = timestamp WHERE created_at IS NULL"))
    bind.execute(sa.text("UPDATE events SET event_time = timestamp WHERE event_time IS NULL"))
    bind.execute(sa.text("UPDATE events SET source_id = source WHERE source_id IS NULL"))
    bind.execute(sa.text("UPDATE events SET plugin_id = plugin WHERE plugin_id IS NULL"))
    bind.execute(sa.text("UPDATE events SET retention_class = 'raw' WHERE retention_class IS NULL"))

    for index_name, table_name, columns, unique in [
        ("ix_assets_hostname", "assets", ["hostname"], False),
        ("ix_assets_is_active", "assets", ["is_active"], False),
        ("ix_assets_name", "assets", ["name"], False),
        ("ix_assets_system_id", "assets", ["system_id"], False),
        ("ix_assets_update_available", "assets", ["update_available"], False),
        ("ix_systems_hostname", "systems", ["hostname"], False),
        ("ix_systems_vmid", "systems", ["vmid"], True),
        ("ix_events_asset_id", "events", ["asset_id"], False),
        ("ix_events_country", "events", ["country"], False),
        ("ix_events_created_at", "events", ["created_at"], False),
        ("ix_events_event_time", "events", ["event_time"], False),
        ("ix_events_event_type", "events", ["event_type"], False),
        ("ix_events_event_type_time", "events", ["event_type", "event_time"], False),
        ("ix_events_hostname", "events", ["hostname"], False),
        ("ix_events_path", "events", ["path"], False),
        ("ix_events_plugin", "events", ["plugin"], False),
        ("ix_events_plugin_id", "events", ["plugin_id"], False),
        ("ix_events_severity", "events", ["severity"], False),
        ("ix_events_source", "events", ["source"], False),
        ("ix_events_source_id", "events", ["source_id"], False),
        ("ix_events_status_code", "events", ["status_code"], False),
        ("ix_plugins_status", "plugins", ["status"], False),
        ("ix_datasources_name", "datasources", ["name"], False),
        ("ix_datasources_plugin_id", "datasources", ["plugin_id"], False),
        ("ix_insights_timestamp", "insights", ["timestamp"], False),
        ("ix_insights_type", "insights", ["type"], False),
        ("ix_insights_ip", "insights", ["ip"], False),
        ("ix_insights_asset_id", "insights", ["asset_id"], False),
        ("ix_actions_timestamp", "actions", ["timestamp"], False),
        ("ix_actions_action_type", "actions", ["action_type"], False),
        ("ix_actions_target", "actions", ["target"], False),
        ("ix_actions_status", "actions", ["status"], False),
        ("ix_aggregations_daily_date", "aggregations_daily", ["date"], False),
        ("ix_aggregations_daily_metric", "aggregations_daily", ["metric"], False),
        ("ix_aggregations_daily_key", "aggregations_daily", ["key"], False),
        ("ix_aggregations_monthly_month", "aggregations_monthly", ["month"], False),
        ("ix_aggregations_monthly_metric", "aggregations_monthly", ["metric"], False),
        ("ix_aggregations_monthly_key", "aggregations_monthly", ["key"], False),
        ("ix_diagnostics_plugin", "diagnostics", ["plugin"], False),
    ]:
        _create_index_if_missing(index_name, table_name, columns, unique=unique)


def downgrade() -> None:
    """Downgrade schema."""
    for index_name, table_name in [
        ("ix_diagnostics_plugin", "diagnostics"),
        ("ix_aggregations_monthly_key", "aggregations_monthly"),
        ("ix_aggregations_monthly_metric", "aggregations_monthly"),
        ("ix_aggregations_monthly_month", "aggregations_monthly"),
        ("ix_aggregations_daily_key", "aggregations_daily"),
        ("ix_aggregations_daily_metric", "aggregations_daily"),
        ("ix_aggregations_daily_date", "aggregations_daily"),
        ("ix_actions_status", "actions"),
        ("ix_actions_target", "actions"),
        ("ix_actions_action_type", "actions"),
        ("ix_actions_timestamp", "actions"),
        ("ix_insights_asset_id", "insights"),
        ("ix_insights_ip", "insights"),
        ("ix_insights_type", "insights"),
        ("ix_insights_timestamp", "insights"),
        ("ix_datasources_plugin_id", "datasources"),
        ("ix_datasources_name", "datasources"),
        ("ix_plugins_status", "plugins"),
        ("ix_systems_vmid", "systems"),
        ("ix_systems_hostname", "systems"),
        ("ix_events_status_code", "events"),
        ("ix_events_source_id", "events"),
        ("ix_events_source", "events"),
        ("ix_events_severity", "events"),
        ("ix_events_plugin_id", "events"),
        ("ix_events_plugin", "events"),
        ("ix_events_path", "events"),
        ("ix_events_hostname", "events"),
        ("ix_events_event_type_time", "events"),
        ("ix_events_event_type", "events"),
        ("ix_events_event_time", "events"),
        ("ix_events_created_at", "events"),
        ("ix_events_country", "events"),
        ("ix_events_asset_id", "events"),
        ("ix_assets_update_available", "assets"),
        ("ix_assets_system_id", "assets"),
        ("ix_assets_name", "assets"),
        ("ix_assets_is_active", "assets"),
        ("ix_assets_hostname", "assets"),
    ]:
        _drop_index_if_exists(index_name, table_name)

    for table_name in [
        "diagnostics",
        "aggregations_monthly",
        "aggregations_daily",
        "actions",
        "insights",
        "datasources",
        "plugins",
    ]:
        if table_name in _table_names():
            op.drop_table(table_name)
