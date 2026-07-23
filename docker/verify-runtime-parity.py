from __future__ import annotations

import re
import sys
from pathlib import Path

PYTHON_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
PYTHON_FROM_PATTERN = re.compile(
    r"FROM python:(?P<version>\d+\.\d+\.\d+)-(?P<variant>[^@\s]+)"
    r"@sha256:(?P<digest>[0-9a-f]{64}) AS (?P<stage>builder|runtime)"
)
EXPECTED_STAGES = {"builder", "runtime"}


def main() -> int:
    repository_root = Path(__file__).resolve().parent.parent
    version_path = repository_root / "backend" / ".python-version"
    dockerfile_path = repository_root / "docker" / "Dockerfile"

    expected_version = version_path.read_text(encoding="utf-8").strip()
    if PYTHON_VERSION_PATTERN.fullmatch(expected_version) is None:
        print(f"{version_path} must contain an exact Python patch version", file=sys.stderr)
        return 1

    stages: dict[str, tuple[str, str, str]] = {}
    for line in dockerfile_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("FROM python:"):
            continue
        match = PYTHON_FROM_PATTERN.fullmatch(line)
        if match is None:
            print(f"Unpinned or unsupported Python base: {line}", file=sys.stderr)
            return 1
        stage = match["stage"]
        if stage in stages:
            print(f"Dockerfile contains duplicate Python stage: {stage}", file=sys.stderr)
            return 1
        stages[stage] = (
            match["version"],
            match["variant"],
            match["digest"],
        )

    if set(stages) != EXPECTED_STAGES:
        print("Dockerfile must contain pinned builder and runtime Python stages", file=sys.stderr)
        return 1

    mismatched = [
        stage
        for stage, (version, _variant, _digest) in stages.items()
        if version != expected_version
    ]
    if mismatched:
        print(
            f"Python {expected_version} is required by {version_path}, but "
            f"{', '.join(sorted(mismatched))} use a different version",
            file=sys.stderr,
        )
        return 1

    if stages["builder"] != stages["runtime"]:
        print("Docker builder and runtime must use the same pinned Python image", file=sys.stderr)
        return 1

    print(f"Development and Docker use Python {expected_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
