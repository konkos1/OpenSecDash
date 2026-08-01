from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

CHECKED_PACKAGES = ("fastapi", "uvicorn", "websockets")


def _canonicalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: verify-image-packages.py UV_LOCK IMAGE_PACKAGES_JSON THIRD_PARTY_NOTICES_JSON",
            file=sys.stderr,
        )
        return 2

    lock_path = Path(sys.argv[1])
    image_packages_path = Path(sys.argv[2])
    notices_path = Path(sys.argv[3])
    lock_data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    locked = {package["name"]: package["version"] for package in lock_data["package"]}
    image_packages = json.loads(image_packages_path.read_text(encoding="utf-8"))
    installed = {_canonicalize_name(package["name"]): package["version"] for package in image_packages}
    notice_data = json.loads(notices_path.read_text(encoding="utf-8"))
    noticed = {
        _canonicalize_name(component["name"]): component["version"]
        for component in notice_data["components"]
        if component["ecosystem"] == "Python"
    }

    mismatches = [
        f"{name}: lock={locked.get(name)!r}, image={installed.get(name)!r}"
        for name in CHECKED_PACKAGES
        if locked.get(name) != installed.get(name)
    ]
    if mismatches:
        print("Image packages do not match uv.lock:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1

    notice_mismatches = [
        f"{name}: image={installed.get(name)!r}, notice={noticed.get(name)!r}"
        for name in sorted(set(installed) | set(noticed))
        if installed.get(name) != noticed.get(name)
    ]
    if notice_mismatches:
        print("Image packages do not match third-party notices:", file=sys.stderr)
        print("\n".join(notice_mismatches), file=sys.stderr)
        return 1

    print("Image versions match uv.lock: " + ", ".join(f"{name}=={installed[name]}" for name in CHECKED_PACKAGES))
    print(f"Third-party notices match all {len(installed)} installed Python packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
