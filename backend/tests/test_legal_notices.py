from pathlib import Path

from app.services.legal_notices import (
    project_license_text,
    third_party_notice_data,
    third_party_notice_text,
    third_party_source_text,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_generated_notices_cover_runtime_browser_and_first_party_assets():
    notices = third_party_notice_data()
    components = {
        (component["name"].lower(), component["version"]): component
        for component in notices["components"]
    }

    assert "backend/app/static/img/**/*.svg" in notices["first_party_assets"]
    assert "website/public/**/*.svg" in notices["first_party_assets"]
    assert components[("alpine.js", "3.15.0")]["license"] == "MIT"
    assert components[("htmx", "1.9.12")]["license"] == "0BSD"
    assert components[("tailwind css", "3.4.17")]["license"] == "MIT"
    assert components[("paho-mqtt", "2.1.0")]["license"] == "EDL-1.0"
    assert components[("certifi", "2026.6.17")]["source_required"] is True
    assert components[("certifi", "2026.6.17")]["source_archive_sha256"] == (
        "024c88eeec92ca068db80f02b8b07c9cef7b9fe261d1d535abfd5abd6f6af432"
    )
    assert components[("certifi", "2026.6.17")]["source_archive_size"] == 134594
    assert all(component["documents"] for component in notices["components"])


def test_every_shipped_svg_has_a_provenance_declaration():
    notices = third_party_notice_data()
    first_party_assets = {
        path
        for pattern in notices["first_party_assets"]
        for path in REPOSITORY_ROOT.glob(pattern)
        if path.is_file()
    }
    third_party_assets = {
        REPOSITORY_ROOT / relative_path
        for component in notices["components"]
        for relative_path in component["distributed_files"]
        if Path(relative_path).suffix.casefold() == ".svg"
    }
    shipped_assets = {
        *REPOSITORY_ROOT.glob("backend/app/static/**/*.svg"),
        *REPOSITORY_ROOT.glob("website/public/**/*.svg"),
    }

    assert shipped_assets <= first_party_assets | third_party_assets


def test_human_readable_notices_include_project_and_dependency_licenses():
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in project_license_text()
    notice = third_party_notice_text()
    assert "# OpenSecDash third-party notices" in notice
    assert "Eclipse Distribution License - v 1.0" in notice
    assert "Zero-Clause BSD" in notice
    assert "opensecdash-copyleft-sources.tar.gz" in third_party_source_text()
