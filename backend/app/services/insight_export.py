"""Bounded, privacy-preserving versioned Insight exports."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

from app.core.logging import redact_sensitive
from app.models.core import Insight
from app.services.insight_catalog import BUILTIN_INSIGHT_DEFINITIONS
from app.services.insights import utc_iso

# Both transports share a closed payload, comfortably below this hard limit.
MAX_INSIGHT_BYTES = 8 * 1024
MAX_COUNT = 2**63 - 1
_TITLES = {definition.type: definition.title for definition in BUILTIN_INSIGHT_DEFINITIONS}
_BUNDLE = Path(__file__).resolve().parents[1] / "insights" / "rules" / "default-rules.json"
_TITLES.update({rule["id"]: rule["title"] for rule in json.loads(_BUNDLE.read_text())["rules"]})
_COUNTS = {"event_count", "distinct_ip_count", "distinct_asset_count", "distinct_country_count",
    "sample_count", "followup_seconds", "followup_gap_seconds"}
_TIMES = {"window_start", "window_end"}
_COMPARISON_COUNTS = {"previous_count", "current_count", "difference"}
_COMPARISON_TIMES = {"previous_start", "previous_end", "current_start", "current_end"}


def insight_display_time() -> ColumnElement[datetime]:
    """Use event time, falling back to creation time only for legacy findings."""
    return func.coalesce(Insight.occurred_at, Insight.timestamp)


def insight_sample_ids(insight: Insight) -> list[int]:
    """Read at most twenty stored example IDs, including legacy data."""
    return list(dict.fromkeys(value for value in (insight.related_event_ids or [])
        if type(value) is int and 0 < value <= MAX_COUNT))[:20]


def insight_certainty(insight: Insight) -> tuple[str, str]:
    """Describe evidence strength independently of finding severity."""
    evidence = insight.evidence if isinstance(insight.evidence, dict) else {}
    confidence = insight.confidence or 0
    if evidence.get("version") == 1 and math.isfinite(confidence) and 0.8 <= confidence <= 1:
        if evidence.get("kind") == "direct":
            return "✓", "certain"
        if evidence.get("kind") in ("heuristic", "correlation", "trend"):
            return "≈", "probable"
    return "?", "possible"


def _summary(values: dict[str, Any], counts: set[str], times: set[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in sorted(counts):
        value = values.get(key)
        if type(value) is int and 0 <= value <= MAX_COUNT:
            summary[key] = value
    for key in sorted(times):
        value = values.get(key)
        if isinstance(value, str) and len(value) <= 40:
            try:
                parsed = datetime.fromisoformat(value)
                if parsed.utcoffset() is not None:
                    summary[key] = utc_iso(parsed)
            except (ValueError, OverflowError):
                pass
    return summary


def serialize_insight(insight: Insight, *, download: bool = False) -> dict[str, Any]:
    """Export only bounded fields; stored free text never crosses this boundary."""
    known_type = insight.type in _TITLES
    export_type = insight.type if known_type else "other." + hashlib.sha256(insight.type.encode()).hexdigest()[:24]
    confidence = insight.confidence
    if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        confidence = 0.0
    evidence = insight.evidence if isinstance(insight.evidence, dict) else {}
    summary = _summary(evidence, _COUNTS, _TIMES) if evidence.get("version") == 1 else {}
    if evidence.get("version") == 1:
        summary["version"] = 1
        if evidence.get("kind") in ("direct", "heuristic", "correlation", "trend"):
            summary["kind"] = evidence["kind"]
        if type(evidence.get("sample_truncated")) is bool:
            summary["sample_truncated"] = evidence["sample_truncated"]
        comparison = evidence.get("comparison")
        if isinstance(comparison, dict):
            summary["comparison"] = _summary(comparison, _COMPARISON_COUNTS, _COMPARISON_TIMES)
    payload: dict[str, Any] = {
        "schema_version": 1, "id": insight.id, "type": export_type,
        "title": redact_sensitive(_TITLES[insight.type])[:255] if known_type else "Insight",
        "level": insight.level if insight.level in ("low", "medium", "high", "critical") else "medium",
        "confidence": confidence, "certainty": insight_certainty(insight)[1],
        "occurred_at": utc_iso(insight.occurred_at) if insight.occurred_at else None,
        "created_at": utc_iso(insight.timestamp), "evidence": summary,
    }
    if download:
        payload["example_event_ids"] = insight_sample_ids(insight)
        payload["detail_path"] = f"/insights/{insight.id}"
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_INSIGHT_BYTES:
        raise ValueError("Insight export exceeds 8 KiB")
    return payload
