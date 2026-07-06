from app.core.template_context import build_template_context
from app.core.version import is_newer_version
from app.models.settings import Setting
import app.services.self_update as self_update_module
from app.services.self_update import LATEST_VERSION_KEY, run_self_update_check


def test_is_newer_version_comparisons():
    assert is_newer_version("v0.2.0", "0.1.3")
    assert is_newer_version("0.1.10", "0.1.9")
    assert is_newer_version("v1.0.0", "0.9.9")
    assert not is_newer_version("v0.1.3", "0.1.3")
    assert not is_newer_version("v0.1.2", "0.1.3")
    assert is_newer_version("v0.2", "0.1.9")  # shorter tuples are padded
    assert is_newer_version("v0.2.0-rc1", "0.1.3")  # tolerate suffixes
    # Unparseable versions (e.g. local "dev" builds) never count as outdated.
    assert not is_newer_version("v0.2.0", "dev")
    assert not is_newer_version("", "0.1.3")
    assert not is_newer_version(None, None)


def test_run_self_update_check_stores_latest_release(monkeypatch, db_session):
    monkeypatch.setattr(self_update_module, "get_latest_github_release", lambda repo, github_token=None: "v9.9.9")

    assert run_self_update_check(db_session) == "v9.9.9"

    stored = db_session.query(Setting).filter_by(key=LATEST_VERSION_KEY).one()
    assert stored.value == "v9.9.9"


def test_run_self_update_check_respects_toggle_and_failures(monkeypatch, db_session):
    db_session.add(Setting(key="update_check_enabled", value="false"))
    db_session.commit()
    monkeypatch.setattr(self_update_module, "get_latest_github_release", lambda repo, github_token=None: "v9.9.9")

    assert run_self_update_check(db_session) is None
    assert db_session.query(Setting).filter_by(key=LATEST_VERSION_KEY).first() is None

    # Network failure: no crash, no stored value, last known value untouched.
    db_session.query(Setting).filter_by(key="update_check_enabled").delete()
    db_session.commit()

    def boom(repo, github_token=None):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(self_update_module, "get_latest_github_release", boom)
    assert run_self_update_check(db_session) is None


def test_footer_update_hint_only_when_newer_and_enabled(monkeypatch, db_session):
    import app.core.template_context as template_context

    monkeypatch.setattr(template_context, "get_app_version", lambda: "0.1.3")

    # No known latest version -> no hint.
    assert build_template_context(db_session)["update_available_version"] is None

    db_session.add(Setting(key=LATEST_VERSION_KEY, value="v0.2.0"))
    db_session.commit()
    assert build_template_context(db_session)["update_available_version"] == "v0.2.0"

    # Same version -> no hint.
    db_session.query(Setting).filter_by(key=LATEST_VERSION_KEY).update({"value": "v0.1.3"})
    db_session.commit()
    assert build_template_context(db_session)["update_available_version"] is None

    # Newer version known but checks disabled -> no hint despite cached value.
    db_session.query(Setting).filter_by(key=LATEST_VERSION_KEY).update({"value": "v0.2.0"})
    db_session.add(Setting(key="update_check_enabled", value="false"))
    db_session.commit()
    assert build_template_context(db_session)["update_available_version"] is None
