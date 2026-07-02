from app.api.pages import update_asset_metadata
from app.models.assets import Asset
from app.models.settings import Setting
from app.models.systems import System


def test_asset_metadata_save_redirects_to_combined_system_view(db_session):
    db_session.add(Setting(key="plugin.json_assets.enabled", value="true"))
    system = System(vmid="100", hostname="apps", system_type="vm")
    db_session.add(system)
    db_session.flush()
    asset = Asset(system_id=system.id, name="Vaultwarden", is_active=True)
    db_session.add(asset)
    db_session.commit()

    response = update_asset_metadata(asset.id, version="1.0.0", host_url="vault.example.com", db=db_session)

    assert response.headers["location"] == f"/assets/system/{system.id}#asset-events"
    assert "asset_id=" not in response.headers["location"]
