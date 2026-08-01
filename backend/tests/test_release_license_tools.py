from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
from typing import cast

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPORT_TOOL = runpy.run_path(
    str(REPOSITORY_ROOT / "docker" / "generate-container-license-report.py")
)
requires_source = cast(Callable[[str], bool], REPORT_TOOL["_requires_source"])


@pytest.mark.parametrize(
    "license_text",
    [
        "Everything else in the package is covered by the GNU GPL.",
        "The overall license is the GNU Lesser General Public License, version 2.1.",
        "License: MPL-2.0",
        "License: CDDL-1.0",
        "Licensed under the European Union Public Licence, version 1.2.",
    ],
)
def test_container_report_detects_reviewed_copyleft_families(license_text):
    assert requires_source(license_text) is True


@pytest.mark.parametrize(
    "license_text",
    [
        "License: BSD-3-Clause",
        "This permissive license is GPL-compatible.",
        "Files: *\nLicense: MIT",
    ],
)
def test_container_report_does_not_mark_permissive_licenses(license_text):
    assert requires_source(license_text) is False


def _source_download_fixture(tmp_path: Path, digest: str) -> tuple[list[str], Path]:
    source_archive = tmp_path / "fixture.tar.gz"
    source_archive.write_bytes(b"covered source fixture\n")
    report_path = tmp_path / "container-report.json"
    report_path.write_text(json.dumps({"packages": []}), encoding="utf-8")
    notices_path = tmp_path / "application-notices.json"
    notices_path.write_text(
        json.dumps(
            {
                "components": [
                    {
                        "name": "fixture",
                        "version": "1.0",
                        "source_required": True,
                        "source_archive": source_archive.resolve().as_uri(),
                        "source_archive_sha256": digest,
                        "source_archive_size": source_archive.stat().st_size,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / f"output-{digest[:8]}"
    command = [
        "bash",
        str(REPOSITORY_ROOT / "docker" / "download-container-sources.sh"),
        str(report_path),
        str(notices_path),
        str(output_path),
    ]
    return command, output_path


def test_python_source_download_verifies_locked_integrity(tmp_path):
    digest = hashlib.sha256(b"covered source fixture\n").hexdigest()
    command, output_path = _source_download_fixture(tmp_path, digest)

    subprocess.run(command, check=True, capture_output=True, text=True)

    assert (output_path / "python" / "fixture-1.0-fixture.tar.gz").read_bytes() == (
        b"covered source fixture\n"
    )


def test_python_source_download_rejects_hash_mismatch(tmp_path):
    command, _ = _source_download_fixture(tmp_path, "0" * 64)

    result = subprocess.run(command, check=False, capture_output=True, text=True)

    assert result.returncode == 1
    assert "Integrity mismatch for Python source archive fixture 1.0" in result.stderr
