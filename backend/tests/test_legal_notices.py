from app.services.legal_notices import project_license_text, third_party_notice_data, third_party_notice_text


def test_generated_notices_cover_runtime_browser_and_first_party_assets():
    notices = third_party_notice_data()
    components = {
        (component["name"].lower(), component["version"]): component
        for component in notices["components"]
    }

    assert "backend/app/static/img/**/*.svg" in notices["first_party_assets"]
    assert components[("alpine.js", "3.15.0")]["license"] == "MIT"
    assert components[("htmx", "1.9.12")]["license"] == "0BSD"
    assert components[("tailwind css", "3.4.17")]["license"] == "MIT"
    assert components[("paho-mqtt", "2.1.0")]["license"] == "EDL-1.0"
    assert components[("certifi", "2026.6.17")]["source_required"] is True
    assert all(component["documents"] for component in notices["components"])


def test_human_readable_notices_include_project_and_dependency_licenses():
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in project_license_text()
    notice = third_party_notice_text()
    assert "# OpenSecDash third-party notices" in notice
    assert "Eclipse Distribution License - v 1.0" in notice
    assert "Zero-Clause BSD" in notice

