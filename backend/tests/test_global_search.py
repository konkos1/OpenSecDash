from app.api.pages import global_search
from app.models.assets import Asset
from app.models.settings import Setting
from app.models.systems import System


def _set(db_session, key: str, value: str) -> None:
    db_session.add(Setting(key=key, value=value))
    db_session.commit()


def test_global_search_routes_ip_and_cidr_to_ip_explorer(db_session):
    _set(db_session, "plugin.traefik_log.enabled", "true")

    assert global_search("1.2.3.4", db=db_session).headers["location"] == "/ip/1.2.3.4"
    assert global_search("2001:db8::/32", db=db_session).headers["location"] == "/ip/2001%3Adb8%3A%3A%2F32"


def test_global_search_routes_known_asset_name_to_asset_explorer(db_session):
    _set(db_session, "plugin.json_assets.enabled", "true")
    system = System(vmid="100", hostname="apps", system_type="vm")
    db_session.add(system)
    db_session.flush()
    db_session.add(Asset(system_id=system.id, name="Nextcloud", hostname="cloud.example.test"))
    db_session.commit()

    assert global_search("Nextcloud", db=db_session).headers["location"] == "/assets?q=Nextcloud"


def test_global_search_routes_other_text_to_events(db_session):
    _set(db_session, "plugin.traefik_log.enabled", "true")

    assert global_search("wp-login.php", db=db_session).headers["location"] == "/events?q=wp-login.php"


def test_global_search_routes_to_dashboard_when_features_are_disabled(db_session):
    assert global_search("https://example.test", db=db_session).headers["location"] == "/"
