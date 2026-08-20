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
    assert components[("alpine.js", "3.15.12")]["license"] == "MIT"
    assert components[("htmx", "2.0.10")]["license"] == "0BSD"
    assert components[("tailwind css", "4.3.3")]["license"] == "MIT"
    assert components[("cffi", "2.1.1")]["license"] == "MIT-0"
    assert components[("greenlet", "3.5.5")]["license"] == "MIT AND PSF-2.0"
    assert components[("paho-mqtt", "2.1.0")]["license"] == "EDL-1.0"
    assert components[("certifi", "2026.7.22")]["source_required"] is True
    assert components[("certifi", "2026.7.22")]["source_archive_sha256"] == (
        "741e2c3b351ddf169a738da9f2c048608ff7f2c5cc02f1ebc6b118bb090d5d55"
    )
    assert components[("certifi", "2026.7.22")]["source_archive_size"] == 138112
    assert all(component["documents"] for component in notices["components"])
    assert all(
        line == line.rstrip()
        for component in notices["components"]
        for document in component["documents"]
        for line in document["text"].splitlines()
    )


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
    assert all(line == line.rstrip() for line in notice.splitlines())
