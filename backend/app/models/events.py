from datetime import datetime

from app.core.time import utc_now

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_event_type_time", "event_type", "event_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Backwards compatible alias used by existing templates/API.
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=True, index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=True, index=True)

    source: Mapped[str] = mapped_column(String(100), default="manual", index=True)
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    plugin: Mapped[str] = mapped_column(String(100), default="core", index=True)
    plugin_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    event_type: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="info", index=True)

    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    asn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    isp: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    asset_id: Mapped[int | None] = mapped_column(nullable=True, index=True)

    method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    path: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    status_code: Mapped[int | None] = mapped_column(nullable=True, index=True)

    data_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    retention_class: Mapped[str | None] = mapped_column(String(20), nullable=True, default="raw")
    # Set once a background pass has attempted GeoIP enrichment for this event
    # (regardless of whether it found data), so ingestion never blocks on a
    # network call and the backfill loop doesn't repeatedly revisit local IPs
    # or already-failed lookups. See services/events.py:enrich_geoip_backlog.
    geoip_checked: Mapped[bool] = mapped_column(Boolean, default=False)
