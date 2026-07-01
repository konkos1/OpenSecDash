from app.api.pages import diagnostic_disabled_message, diagnostic_plugin_enabled
from app.models.settings import Setting


def _set(db_session, key: str, value: str) -> None:
    existing = db_session.query(Setting).filter(Setting.key == key).first()
    if existing is None:
        db_session.add(Setting(key=key, value=value))
    else:
        existing.value = value
    db_session.commit()


def test_geoip_diagnostic_disabled_without_event_source(db_session):
    _set(db_session, "plugin.geoip.enabled", "true")

    assert diagnostic_plugin_enabled(db_session, "geoip") is False
    assert diagnostic_disabled_message(db_session, "geoip") == "No event datasource plugin is enabled."


def test_geoip_diagnostic_enabled_with_event_source(db_session):
    _set(db_session, "plugin.geoip.enabled", "true")
    _set(db_session, "plugin.traefik_log.enabled", "true")

    assert diagnostic_plugin_enabled(db_session, "geoip") is True
