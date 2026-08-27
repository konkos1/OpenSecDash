#!/usr/bin/env python3
"""Validate narrow, time-bounded Trivy vulnerability exceptions."""

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys
from typing import Any


REQUIRED_FIELDS = {"id", "expired_at", "statement"}


def validate_ignore_file(path: Path, *, now: datetime | None = None) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read JSON-compatible YAML from {path}: {exc}") from exc

    if not isinstance(document, dict) or set(document) != {"vulnerabilities"}:
        raise ValueError("The ignore file must contain only a vulnerabilities list")
    exceptions = document["vulnerabilities"]
    if not isinstance(exceptions, list):
        raise ValueError("vulnerabilities must be a list")

    effective_now = now or datetime.now(UTC)
    if effective_now.tzinfo is None:
        raise ValueError("The validation time must include a timezone")
    identifiers: set[str] = set()
    for position, exception in enumerate(exceptions, start=1):
        if not isinstance(exception, dict) or set(exception) != REQUIRED_FIELDS:
            raise ValueError(
                f"Exception {position} must contain exactly: {', '.join(sorted(REQUIRED_FIELDS))}"
            )
        identifier = exception["id"]
        statement = exception["statement"]
        expiry_text = exception["expired_at"]
        if not isinstance(identifier, str) or re.fullmatch(r"CVE-\d{4}-\d{4,}", identifier) is None:
            raise ValueError(f"Exception {position} must name one CVE")
        if identifier in identifiers:
            raise ValueError(f"Duplicate vulnerability exception: {identifier}")
        identifiers.add(identifier)
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError(f"Exception {identifier} must explain its limited scope")
        if not isinstance(expiry_text, str):
            raise ValueError(f"Exception {identifier} must have an ISO expiry date")
        if not expiry_text.endswith("Z"):
            raise ValueError(f"Exception {identifier} must have a UTC expiry timestamp")
        try:
            expiry = datetime.fromisoformat(expiry_text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Exception {identifier} has an invalid expiry timestamp") from exc
        if expiry <= effective_now:
            raise ValueError(f"Exception {identifier} expired on {expiry.date().isoformat()}")

    return exceptions


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".trivyignore.yaml")
    try:
        exceptions = validate_ignore_file(path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    for exception in exceptions:
        print(f"Allowed until {exception['expired_at']}: {exception['id']} - {exception['statement']}")


if __name__ == "__main__":
    main()
