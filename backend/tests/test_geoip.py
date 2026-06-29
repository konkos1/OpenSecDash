from datetime import timedelta

from app.core.time import utc_now
from app.models.core import GeoIPCache
from app.models.settings import Setting
from app.services.geoip import enrich_event_values, lookup_geoip, normalize_asn, normalize_isp


def test_geoip_normalizes_asn_and_truncates_isp():
    assert normalize_asn("15169 Google LLC") == "AS15169"
    assert normalize_asn("AS8075 Microsoft") == "AS8075"
    assert normalize_asn("not-an-asn") is None
    assert normalize_isp("  Example ISP  ") == "Example ISP"
    assert len(normalize_isp("x" * 300) or "") == 255


def test_geoip_cache_is_used_and_plugin_values_win(db_session):
    db_session.add_all(
        [
            Setting(key="plugin.geoip.enabled", value="true"),
            Setting(key="plugin.geoip.provider", value="ip-api"),
            GeoIPCache(
                lookup_key="8.8.8.8",
                provider="ip-api",
                country="US",
                asn="AS15169",
                isp="Google LLC",
                looked_up_at=utc_now().replace(tzinfo=None),
                expires_at=(utc_now() + timedelta(days=1)).replace(tzinfo=None),
            ),
        ]
    )
    db_session.commit()

    assert lookup_geoip(db_session, "8.8.8.8", require_asn=True, require_isp=True) == ("US", "AS15169", "Google LLC")

    values = {"ip": "8.8.8.8", "country": "DE"}
    enrich_event_values(db_session, values)
    assert values == {"ip": "8.8.8.8", "country": "DE", "asn": "AS15169", "isp": "Google LLC"}

    producer_values = {"ip": "8.8.8.8", "country": "DE", "asn": "AS64500", "isp": "Producer ISP"}
    enrich_event_values(db_session, producer_values)
    assert producer_values == {"ip": "8.8.8.8", "country": "DE", "asn": "AS64500", "isp": "Producer ISP"}
