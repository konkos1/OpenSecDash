from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

CHECKED_PACKAGES = ("fastapi", "uvicorn", "websockets")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: verify-image-packages.py UV_LOCK IMAGE_PACKAGES_JSON", file=sys.stderr)
        return 2

    lock_path = Path(sys.argv[1])
    image_packages_path = Path(sys.argv[2])
    lock_data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    locked = {package["name"]: package["version"] for package in lock_data["package"]}
    image_packages = json.loads(image_packages_path.read_text(encoding="utf-8"))
    installed = {package["name"].lower(): package["version"] for package in image_packages}

    mismatches = [
        f"{name}: lock={locked.get(name)!r}, image={installed.get(name)!r}"
        for name in CHECKED_PACKAGES
        if locked.get(name) != installed.get(name)
    ]
    if mismatches:
        print("Image packages do not match uv.lock:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1

    print("Image versions match uv.lock: " + ", ".join(f"{name}=={installed[name]}" for name in CHECKED_PACKAGES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
