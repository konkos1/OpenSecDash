from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.models.saved_views import SavedView


VIEW_SCOPES = {"events", "access"}
MAX_VIEW_NAME_LENGTH = 120
MAX_FILTER_VALUE_LENGTH = 2048
MAX_FILTER_LIST_VALUES = 20
_TIME_RANGES = {"all", "1h", "24h", "7d", "30d", "custom"}


def copy_legacy_views_to_user(db: Session, user_id: int) -> None:
    """Initialize a user's saved views from the anonymous legacy views."""
    for view in db.query(SavedView).filter(SavedView.user_id.is_(None)).all():
        db.add(
            SavedView(
                user_id=user_id,
                name=view.name,
                scope=view.scope,
                filter_json=view.filter_json,
                query_json=view.query_json,
            )
        )


def clean_view_name(value: object) -> str | None:
    name = str(value).strip()
    return name if 0 < len(name) <= MAX_VIEW_NAME_LENGTH else None


def _text(value: object, max_length: int = MAX_FILTER_VALUE_LENGTH) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and len(text) <= max_length else None


def _integer(value: object) -> int | None:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return None
    return result if 0 <= result <= 999 else None


def _country(value: object) -> str | None:
    country = _text(value, 2)
    if country == "-":
        return country
    if country and re.fullmatch(r"[A-Za-z]{2}", country):
        return country.upper()
    return None


def _truthy(value: object) -> bool:
    return value is True or str(value).lower() == "true"


def _datetime_text(value: object) -> str | None:
    text = _text(value, 64)
    if text is None:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


def validate_view_filters(filters: Mapping[str, object]) -> dict[str, Any]:
    """Return only validated filter keys that can be represented in page URLs."""
    result: dict[str, Any] = {}
    for key, max_length in {
        "event_type": 50,
        "insight_type": 100,
        "ip": 64,
        "severity": 20,
        "source": 100,
        "plugin": 100,
        "asn": 32,
        "hostname": 255,
        "asset": 255,
        "path": 2048,
        "q": 2048,
    }.items():
        if (value := _text(filters.get(key), max_length)) is not None:
            result[key] = value

    if (country := _country(filters.get("country"))) is not None:
        result["country"] = country
    if (country_not := _country(filters.get("country_not"))) not in {None, "-"}:
        result["country_not"] = country_not

    country_values = filters.get("country_in", [])
    if isinstance(country_values, str):
        country_values = country_values.split(",")
    if isinstance(country_values, (list, tuple)):
        countries = [country for value in country_values if (country := _country(value)) not in {None, "-"}]
        if countries:
            result["country_in"] = countries[:MAX_FILTER_LIST_VALUES]

    for key in ["status_code", "status_code_min", "status_code_max"]:
        if (value := _integer(filters.get(key))) is not None:
            result[key] = value

    for key in ["show_local_ips", "hide_local_ips", "include_raw_data"]:
        if _truthy(filters.get(key)):
            result[key] = True
    return result


def view_filters_from_query(items: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Map page query parameters to the canonical saved-view filter format."""
    values: dict[str, object] = {}
    countries: list[str] = []
    for key, value in items:
        if key == "country_in":
            countries.extend(value.split(","))
        elif key == "status_min":
            values["status_code_min"] = value
        elif key == "status_max":
            values["status_code_max"] = value
        else:
            values[key] = value
    if countries:
        values["country_in"] = countries
    return validate_view_filters(values)


def view_query_state_from_query(items: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Return validated route state that is not part of apply_event_filters."""
    values = {key: value for key, value in items}
    result: dict[str, Any] = {}
    if (range_value := _text(values.get("range"), 10)) in _TIME_RANGES:
        result["range"] = range_value
    for key in ["from", "to", "snapshot_before"]:
        if (value := _datetime_text(values.get(key))) is not None:
            result[key] = value
    try:
        hour = int(str(values.get("hour", "")))
    except (TypeError, ValueError):
        hour = None
    if hour is not None and 0 <= hour <= 23:
        result["hour"] = hour
    for key in ["today", "local_ip_filter"]:
        if _truthy(values.get(key)) or values.get(key) == "1":
            result[key] = "true" if key == "today" else "1"
    return result


def view_to_query(filters: Mapping[str, object], query_state: Mapping[str, object] | None = None) -> str:
    """Serialize validated saved-view filters and route state into a page query string."""
    validated = validate_view_filters(filters)
    params: list[tuple[str, str]] = []
    for key in ["event_type", "insight_type", "ip", "severity", "source", "plugin", "country", "country_not", "asn", "hostname", "asset", "path", "q"]:
        if key in validated:
            params.append((key, str(validated[key])))
    if "country_in" in validated:
        params.append(("country_in", ",".join(str(value) for value in validated["country_in"])))
    for filter_key, query_key in [("status_code", "status_code"), ("status_code_min", "status_min"), ("status_code_max", "status_max")]:
        if filter_key in validated:
            params.append((query_key, str(validated[filter_key])))
    for key in ["show_local_ips", "hide_local_ips", "include_raw_data"]:
        if validated.get(key):
            params.append((key, "true"))
    state = view_query_state_from_query((str(key), str(value)) for key, value in (query_state or {}).items())
    for key in ["range", "from", "to", "today", "hour", "snapshot_before", "local_ip_filter"]:
        if key in state:
            params.append((key, str(state[key])))
    return urlencode(params)


def plugin_views_for_scope(views: Iterable[Mapping[str, object]], scope: str) -> list[dict[str, Any]]:
    """Validate plugin-provided view descriptors before rendering them."""
    result = []
    for view in views:
        if view.get("scope") != scope or not (name := clean_view_name(view.get("name", ""))):
            continue
        filters = view.get("filter", {})
        if not isinstance(filters, Mapping):
            continue
        result.append(
            {
                "name": name,
                "filter_json": validate_view_filters(filters),
                "plugin_id": str(view.get("plugin_id", "")),
            }
        )
    return result
