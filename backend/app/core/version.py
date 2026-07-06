from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version

APP_NAME = "OpenSecDash"
PACKAGE_NAME = "backend"
VERSION_ENV_VAR = "OPENSECDASH_VERSION"
FALLBACK_VERSION = "dev"


def get_app_version() -> str:
    env_version = os.getenv(VERSION_ENV_VAR, "").strip()
    if env_version:
        return env_version
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return FALLBACK_VERSION


def _version_tuple(value: str) -> tuple[int, ...] | None:
    text = value.strip().lstrip("vV")
    if not text:
        return None
    parts: list[int] = []
    for part in text.split("."):
        # Tolerate suffixes like "1.2.3-rc1" by reading the leading digits.
        digits = ""
        for char in part:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            return None
        parts.append(int(digits))
    return tuple(parts)


def is_newer_version(candidate: str | None, current: str | None) -> bool:
    """True when ``candidate`` (e.g. a GitHub tag ``v1.2.3``) is newer than ``current``.

    Unparseable versions - notably the ``dev`` fallback of local checkouts -
    never compare as outdated, so development instances aren't nagged about
    "updates" they can't meaningfully install.
    """
    candidate_tuple = _version_tuple(candidate or "")
    current_tuple = _version_tuple(current or "")
    if candidate_tuple is None or current_tuple is None:
        return False
    length = max(len(candidate_tuple), len(current_tuple))
    candidate_padded = candidate_tuple + (0,) * (length - len(candidate_tuple))
    current_padded = current_tuple + (0,) * (length - len(current_tuple))
    return candidate_padded > current_padded
