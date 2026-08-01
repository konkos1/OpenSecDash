"""Load the generated legal notices shipped with the application."""

from functools import lru_cache
import json
from pathlib import Path
from typing import Any


APP_DIRECTORY = Path(__file__).resolve().parents[1]
PROJECT_DIRECTORY = Path(__file__).resolve().parents[3]
NOTICE_DATA_PATH = APP_DIRECTORY / "legal" / "third-party-notices.json"
THIRD_PARTY_NOTICE_PATH = PROJECT_DIRECTORY / "THIRD_PARTY_NOTICES.md"
THIRD_PARTY_SOURCE_PATH = PROJECT_DIRECTORY / "THIRD_PARTY_SOURCE.md"
PROJECT_LICENSE_PATH = PROJECT_DIRECTORY / "LICENSE"


@lru_cache(maxsize=1)
def third_party_notice_data() -> dict[str, Any]:
    """Return the generated component inventory and verbatim notices."""
    return json.loads(NOTICE_DATA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def third_party_notice_text() -> str:
    """Return the complete generated human-readable notice file."""
    return THIRD_PARTY_NOTICE_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def project_license_text() -> str:
    """Return the project's GNU AGPL-3.0 license text."""
    return PROJECT_LICENSE_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def third_party_source_text() -> str:
    """Return the source-availability information shipped with releases."""
    return THIRD_PARTY_SOURCE_PATH.read_text(encoding="utf-8")
