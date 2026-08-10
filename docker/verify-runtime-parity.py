from __future__ import annotations

import re
import sys
from pathlib import Path

PYTHON_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
PYTHON_FROM_PATTERN = re.compile(
    r"FROM python:(?P<version>\d+\.\d+\.\d+)-(?P<variant>[^@\s]+)"
    r"@sha256:(?P<digest>[0-9a-f]{64}) AS (?P<stage>builder|runtime)"
)
UV_FROM_PATTERN = re.compile(
    r"FROM ghcr\.io/astral-sh/uv:(?P<version>\d+\.\d+\.\d+)"
    r"@sha256:(?P<digest>[0-9a-f]{64}) AS uv"
)
SETUP_UV_PATTERN = re.compile(
    r"uses: astral-sh/setup-uv@[^\n]+\n"
    r"\s+with:\n"
    r'\s+version: "(?P<version>\d+\.\d+\.\d+)"'
)
UV_DOCUMENTATION_PATTERN = re.compile(
    r"officially pinned `uv` (?P<version>\d+\.\d+\.\d+) release"
)
EXPECTED_STAGES = {"builder", "runtime"}
UV_WORKFLOW_PATHS = (
    Path(".github/workflows/tests.yml"),
    Path(".github/workflows/docker-publish.yml"),
)
UV_DOCUMENTATION_PATH = Path("website/guide/installation/bare-metal.md")


def _extract_version(path: Path, pattern: re.Pattern[str], description: str) -> str:
    text = path.read_text(encoding="utf-8")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"{path} must contain exactly one {description} version pin")
    return matches[0]["version"]


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

    try:
        uv_version = _extract_version(dockerfile_path, UV_FROM_PATTERN, "Docker uv")
        uv_versions = {
            str(path): _extract_version(
                repository_root / path,
                SETUP_UV_PATTERN,
                "setup-uv",
            )
            for path in UV_WORKFLOW_PATHS
        }
        uv_versions[str(UV_DOCUMENTATION_PATH)] = _extract_version(
            repository_root / UV_DOCUMENTATION_PATH,
            UV_DOCUMENTATION_PATTERN,
            "documented uv",
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    mismatched_uv = [
        f"{path} ({version})"
        for path, version in uv_versions.items()
        if version != uv_version
    ]
    if mismatched_uv:
        print(
            f"Docker uses uv {uv_version}, but these pins differ: "
            f"{', '.join(mismatched_uv)}",
            file=sys.stderr,
        )
        return 1

    print(f"Development and Docker use Python {expected_version}")
    print(f"Docker, CI, and installation documentation use uv {uv_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
