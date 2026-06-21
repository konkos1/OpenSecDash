from datetime import datetime

from sqlalchemy import Boolean, DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PluginRecord(Base):
    __tablename__ = "plugins"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(50), default="1.0.0")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_version: Mapped[str] = mapped_column(String(20), default="1")
    capabilities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="healthy")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Datasource(Base):
    __tablename__ = "datasources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    plugin_id: Mapped[str] = mapped_column(String(100), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source_type: Mapped[str] = mapped_column(String(50), default="logfile")
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="disabled")
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    events_processed: Mapped[int] = mapped_column(default=0)


class Insight(Base):
    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    type: Mapped[str] = mapped_column(String(100), index=True)
    confidence: Mapped[float] = mapped_column(default=0.0)
    level: Mapped[str] = mapped_column(String(20), default="medium")
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    related_event_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    asset_id: Mapped[int | None] = mapped_column(nullable=True, index=True)


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    action_type: Mapped[str] = mapped_column(String(100), index=True)
    plugin_id: Mapped[str] = mapped_column(String(100), default="core")
    target_type: Mapped[str] = mapped_column(String(50), default="ip")
    target: Mapped[str] = mapped_column(String(255), index=True)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)


class AggregationDaily(Base):
    __tablename__ = "aggregations_daily"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    metric: Mapped[str] = mapped_column(String(100), index=True)
    key: Mapped[str] = mapped_column(String(255), index=True)
    value: Mapped[int] = mapped_column(default=0)


class AggregationMonthly(Base):
    __tablename__ = "aggregations_monthly"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    metric: Mapped[str] = mapped_column(String(100), index=True)
    key: Mapped[str] = mapped_column(String(255), index=True)
    value: Mapped[int] = mapped_column(default=0)


class Diagnostic(Base):
    __tablename__ = "diagnostics"

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin: Mapped[str] = mapped_column(String(100), index=True)
    component: Mapped[str] = mapped_column(String(100), default="plugin")
    status: Mapped[str] = mapped_column(String(20), default="healthy")
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
