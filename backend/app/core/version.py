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
