from __future__ import annotations

import json
from pathlib import Path
import platform
import re
import subprocess
import sys
from urllib.parse import quote


COPYLEFT_PATTERN = re.compile(r"\b(?:A?GPL|LGPL|MPL|EPL)[- v]?[0-9]", re.IGNORECASE)
DPKG_FORMAT = "${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\\n"


def _package_rows() -> list[dict[str, object]]:
    result = subprocess.run(
        ["dpkg-query", "-W", f"-f={DPKG_FORMAT}"],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = []
    missing = []
    for line in result.stdout.splitlines():
        binary_package, binary_version, source_package, source_version = line.split("\t")
        package_name = binary_package.split(":", maxsplit=1)[0]
        source_package = source_package or package_name
        source_version = source_version or binary_version
        encoded_source_package = quote(source_package, safe="")
        encoded_source_version = quote(source_version, safe="")
        copyright_path = Path("/usr/share/doc") / package_name / "copyright"
        if not copyright_path.is_file():
            missing.append(str(copyright_path))
            continue
        copyright_text = copyright_path.read_text(encoding="utf-8", errors="replace")
        rows.append(
            {
                "binary_package": binary_package,
                "binary_version": binary_version,
                "copyright_file": str(copyright_path),
                "source_package": source_package,
                "source_version": source_version,
                "source_required": bool(COPYLEFT_PATTERN.search(copyright_text)),
                "source_api_url": (
                    "https://snapshot.debian.org/mr/package/"
                    f"{encoded_source_package}/{encoded_source_version}/srcfiles"
                ),
                "source_url": (
                    "https://snapshot.debian.org/package/"
                    f"{encoded_source_package}/{encoded_source_version}/"
                ),
            }
        )
    if missing:
        print("Installed Debian packages without copyright evidence:", file=sys.stderr)
        print("\n".join(sorted(missing)), file=sys.stderr)
        raise SystemExit(1)
    return sorted(rows, key=lambda row: str(row["binary_package"]))


def main() -> int:
    python_version = platform.python_version()
    report = {
        "schema_version": 1,
        "scope": "Final OpenSecDash release container",
        "python_base": {
            "version": python_version,
            "license": "PSF-2.0",
            "source_url": (
                f"https://www.python.org/ftp/python/{python_version}/"
                f"Python-{python_version}.tar.xz"
            ),
        },
        "packages": _package_rows(),
    }
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
