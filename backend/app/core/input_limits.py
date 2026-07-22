from __future__ import annotations

import json
from typing import Any


# These limits leave room for verbose homelab inventories and raw security
# events while preventing a single request or nested value from consuming
# unbounded memory.
MAX_REQUEST_BODY_BYTES = 12 * 1024 * 1024
MAX_ASSET_INVENTORY_BYTES = 10 * 1024 * 1024
MAX_EVENT_DATA_JSON_BYTES = 1 * 1024 * 1024
MAX_EVENT_RAW_DATA_LENGTH = 1 * 1024 * 1024
MAX_EVENT_REQUEST_BODY_BYTES = 2 * 1024 * 1024 + 128 * 1024
MAX_JSON_DEPTH = 20
MAX_ASSET_SYSTEMS = 10_000
MAX_ASSET_APPS_PER_SYSTEM = 1_000
MAX_ASSET_FIELD_LENGTH = 2_048


def json_depth(value: Any) -> int:
    """Return the maximum nesting depth of JSON-compatible data."""
    if isinstance(value, dict):
        return 1 + max((json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((json_depth(item) for item in value), default=0)
    return 1


def serialized_json_size(value: Any) -> int:
    """Return compact UTF-8 JSON size without retaining another large string."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
