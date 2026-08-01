from collections.abc import Callable
from pathlib import Path
import runpy
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
