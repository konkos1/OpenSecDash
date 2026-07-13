from __future__ import annotations

import re
from collections.abc import Iterable


_ACTION_REFERENCE_RE = re.compile(r"\s+\(action #\d+\)$", re.IGNORECASE)


def normalize_rollup_key(metric: str, key: object) -> str:
    """Return the stable grouping key used by rollup-backed UI widgets."""
    value = str(key)
    if metric == "scenario":
        # OpenSecDash includes the action id in a manual CrowdSec ban reason so
        # the later CrowdSec log entry can be correlated with that exact action.
        # It is operational metadata, not part of the scenario's display group.
        return _ACTION_REFERENCE_RE.sub("", value)
    return value


def combine_rollup_values(metric: str, rows: Iterable[tuple[object, int | None]]) -> dict[str, int]:
    """Combine stored rollup rows after applying their display grouping key."""
    totals: dict[str, int] = {}
    for key, value in rows:
        normalized_key = normalize_rollup_key(metric, key)
        totals[normalized_key] = totals.get(normalized_key, 0) + int(value or 0)
    return totals
