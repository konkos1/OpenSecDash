from app.core.version import get_app_version


def test_app_version_prefers_environment(monkeypatch):
    monkeypatch.setenv("OPENSECDASH_VERSION", "1.2.3")

    assert get_app_version() == "1.2.3"


def test_app_version_ignores_blank_environment(monkeypatch):
    monkeypatch.setenv("OPENSECDASH_VERSION", "   ")

    assert get_app_version() != "   "
