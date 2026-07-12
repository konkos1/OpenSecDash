from datetime import datetime

from app.core.net import is_local_ip_value
from app.core.time import utc_now

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def _derive_is_local_ip(context) -> bool:
    return is_local_ip_value(context.get_current_parameters().get("ip"))


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_event_type_time", "event_type", "event_time"),
        # Partial index for the GeoIP backfill loop, which polls for
        # unchecked events every few seconds forever. Without it that poll is
        # a full table scan once the table has grown; with it the index only
        # ever contains the (usually near-zero) pending rows.
        Index("ix_events_geoip_pending", "geoip_checked", sqlite_where=text("geoip_checked = 0")),
        # Dedupe lookup (find_duplicate_event) runs once per stored event and
        # matches on raw_data. Without this index it degrades to scanning
        # every existing event of the same type per insert - quadratic import
        # time on a log where all lines share one event type (the norm). The
        # index carries full raw_data text, trading disk space for O(log n)
        # ingestion.
        Index("ix_events_dedupe_raw", "plugin", "event_type", "raw_data"),
        # Dashboard and event list queries filter by plugin/type/locality plus
        # a recent event_time window. These composite indexes prevent SQLite
        # from scanning every row for a plugin on larger homelab databases.
        Index("ix_events_plugin_time", "plugin", "event_time"),
        Index("ix_events_plugin_type_time", "plugin", "event_type", "event_time"),
        Index("ix_events_plugin_local_time", "plugin", "is_local_ip", "event_time"),
        Index("ix_events_country_time", "country", "event_time"),
        Index("ix_events_plugin_country_time", "plugin", "country", "event_time"),
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
    asn: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
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
    # Precomputed is_local_ip_value(ip), derived automatically at insert time
    # (and set explicitly by store_event). The local/private classification
    # needs Python's ipaddress module, so filtering on it used to require
    # pulling every candidate row out of the database - this makes the
    # dashboard's internal/external counters and the Access page's
    # hide/show-local filters plain SQL conditions instead.
    is_local_ip: Mapped[bool] = mapped_column(Boolean, default=_derive_is_local_ip)
