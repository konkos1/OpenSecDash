from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable


_ACTION_REFERENCE_RE = re.compile(r"\s+\(action #\d+\)$", re.IGNORECASE)
_ROLLUP_GROUPERS: dict[str, Callable[[str, str], str | None]] = {}

logger = logging.getLogger(__name__)

# Daily precision backs the rolling Dashboard trend. Completed calendar months
# that overlap this window stay intact until the whole month can be compacted.
DAILY_ROLLUP_WINDOW_DAYS = 30


def clear_rollup_groupers() -> None:
    """Remove plugin-provided rollup grouping hooks before rediscovery."""
    _ROLLUP_GROUPERS.clear()


def register_rollup_grouper(plugin_id: str, grouper: Callable[[str, str], str | None]) -> None:
    """Register one plugin's stable grouping hook for rollup keys."""
    _ROLLUP_GROUPERS[plugin_id] = grouper


def normalize_rollup_key(metric: str, key: object) -> str:
    """Return the stable grouping key used by rollup-backed UI widgets."""
    value = str(key)
    if metric == "scenario":
        # OpenSecDash includes the action id in a manual CrowdSec ban reason so
        # the later CrowdSec log entry can be correlated with that exact action.
        # It is operational metadata, not part of the scenario's display group.
        value = _ACTION_REFERENCE_RE.sub("", value)
    for plugin_id, grouper in _ROLLUP_GROUPERS.items():
        try:
            grouped_value = grouper(metric, value)
        except Exception:
            logger.exception("Rollup grouping hook failed for plugin %s", plugin_id)
            continue
        if grouped_value:
            value = grouped_value
    return value


def combine_rollup_values(metric: str, rows: Iterable[tuple[object, int | None]]) -> dict[str, int]:
    """Combine stored rollup rows after applying their display grouping key."""
    totals: dict[str, int] = {}
    for key, value in rows:
        normalized_key = normalize_rollup_key(metric, key)
        totals[normalized_key] = totals.get(normalized_key, 0) + int(value or 0)
    return totals
