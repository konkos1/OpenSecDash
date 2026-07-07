import json
from pathlib import Path
from typing import Any

import requests


def load_asset_source(
    source_type: str,
    source: str,
) -> dict[str, Any]:
    if source_type == "url":
        response = requests.get(
            source,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    if source_type == "file":
        path = Path(source)

        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    raise ValueError(
        f"Unsupported asset source type: {source_type}"
    )
