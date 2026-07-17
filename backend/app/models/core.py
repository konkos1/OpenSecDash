from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text, UniqueConstraint

from app.core.time import utc_now
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
    status: Mapped[str] = mapped_column(String(20), default="healthy", index=True)
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
    backlog_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    backlog_progress_percent: Mapped[int | None] = mapped_column(nullable=True)


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (Index("ix_insights_type_ip_timestamp", "type", "ip", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    type: Mapped[str] = mapped_column(String(100), index=True)
    confidence: Mapped[float] = mapped_column(default=0.0)
    level: Mapped[str] = mapped_column(String(20), default="medium")
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    related_event_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    asset_id: Mapped[int | None] = mapped_column(nullable=True, index=True)


class InsightRule(Base):
    __tablename__ = "insight_rules"
    __table_args__ = (UniqueConstraint("rule_id", name="uq_insight_rule_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(120), index=True)
    source: Mapped[str] = mapped_column(String(50), default="bundled", index=True)
    schema_version: Mapped[str] = mapped_column(String(20), default="1")
    ruleset_version: Mapped[str] = mapped_column(String(20), default="")
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(20), default="medium")
    confidence: Mapped[float] = mapped_column(default=0.7)
    event_types: Mapped[list] = mapped_column(JSON, default=list)
    path_contains_any: Mapped[list] = mapped_column(JSON, default=list)
    group_by: Mapped[str] = mapped_column(String(50), default="ip")
    window_minutes: Mapped[int] = mapped_column(default=5)
    threshold: Mapped[int] = mapped_column(default=1)
    min_distinct_ips: Mapped[int] = mapped_column(default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
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


class CrowdSecDecision(Base):
    __tablename__ = "crowdsec_decisions"
    __table_args__ = (UniqueConstraint("decision_id", name="uq_crowdsec_decision_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(100), index=True)
    ip: Mapped[str] = mapped_column(String(128), index=True)
    scope: Mapped[str | None] = mapped_column(String(50), nullable=True)
    decision_type: Mapped[str] = mapped_column(String(50), index=True)
    origin: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scenario: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration: Mapped[str | None] = mapped_column(String(100), nullable=True)
    until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class GeoIPCache(Base):
    __tablename__ = "geoip_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    lookup_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    asn: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    isp: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    looked_up_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Diagnostic(Base):
    __tablename__ = "diagnostics"

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin: Mapped[str] = mapped_column(String(100), index=True)
    component: Mapped[str] = mapped_column(String(100), default="plugin")
    status: Mapped[str] = mapped_column(String(20), default="healthy")
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationRule(Base):
    __tablename__ = "notification_rules"
    __table_args__ = (UniqueConstraint("rule_id", name="uq_notification_rule_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(120), index=True)
    name: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(20), default="event")
    match_types: Mapped[list] = mapped_column(JSON, default=list)
    min_severity: Mapped[str] = mapped_column(String(20), default="info")
    countries: Mapped[list] = mapped_column(JSON, default=list)
    asset_id: Mapped[int | None] = mapped_column(nullable=True)
    channel: Mapped[str] = mapped_column(String(50), default="email")
    min_count: Mapped[int] = mapped_column(default=1)
    window_minutes: Mapped[int] = mapped_column(default=10)
    cooldown_minutes: Mapped[int] = mapped_column(default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_rule_created", "rule_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    rule_id: Mapped[str] = mapped_column(String(120), index=True)
    channel: Mapped[str] = mapped_column(String(50), default="email")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
