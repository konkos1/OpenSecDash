from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def resolve_timezone(timezone_name: str | None) -> ZoneInfo:
    if not timezone_name or timezone_name == "auto":
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def format_datetime_for_timezone(value: datetime | None, timezone_name: str | None) -> str:
    if value is None:
        return "-"
    local_value = as_utc(value).astimezone(resolve_timezone(timezone_name))
    return local_value.strftime("%Y-%m-%d %H:%M:%S")


def datetime_iso_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    return as_utc(value).isoformat()


def local_day_start_as_utc(timezone_name: str | None) -> datetime:
    timezone = resolve_timezone(timezone_name)
    local_now = utc_now().astimezone(timezone)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(UTC).replace(tzinfo=None)
