import json
from pathlib import Path

import pytest

from conftest import import_plugin_module

from app.core.http_responses import ResponseBodyError
from app.models.settings import Setting
from app.plugins.manager import PluginManager
from app.services.geoip import service as geoip_service
from app.services.geoip.providers import PROVIDERS, get_provider, ip_api
from app.services.geoip.providers.base import GeoIPLookupRequest, GeoIPProviderError

SERVICE_SOURCE = Path(geoip_service.__file__).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, http_error: Exception | None = None):
        self.body = body
        self.headers = headers or {}
        self.http_error = http_error
        self.closed = False

    def iter_content(self, chunk_size):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index : index + chunk_size]

    def raise_for_status(self):
        if self.http_error is not None:
            raise self.http_error

    def close(self):
        self.closed = True


def _request(**overrides) -> GeoIPLookupRequest:
    return GeoIPLookupRequest(ip=overrides.get("ip", "203.0.113.7"), timeout=overrides.get("timeout", 3), settings={})


def _patch_get(monkeypatch, response: FakeResponse) -> list[tuple[tuple, dict]]:
    calls: list[tuple[tuple, dict]] = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(ip_api.requests, "get", fake_get)
    return calls


def test_registry_resolves_only_the_statically_registered_providers():
    assert sorted(PROVIDERS) == ["ip-api"]
    assert get_provider("ip-api") is ip_api.PROVIDER

    with pytest.raises(ValueError, match="Unsupported GeoIP provider: iplocate"):
        get_provider("iplocate")
    with pytest.raises(ValueError, match="Unsupported GeoIP provider: made-up"):
        get_provider("made-up")


def test_public_geoip_imports_stay_available_from_the_package():
    from app.services.geoip import (  # noqa: F401 - importability is the assertion
        cleanup_expired_cache,
        enrich_event_values,
        enrich_pending_events,
        geoip_enabled,
        lookup_country,
        lookup_geoip,
        normalize_asn,
        normalize_city,
        normalize_isp,
        normalize_lookup_target,
    )


def test_service_holds_no_provider_url_or_wire_field_names():
    # The provider boundary only pays off if endpoint details cannot leak back
    # into the shared orchestration.
    for needle in ("ip-api.com", "http://", "countryCode", '"as"', "status,countryCode"):
        assert needle not in SERVICE_SOURCE


def test_ip_api_sends_the_unchanged_streamed_request(monkeypatch):
    response = FakeResponse(json.dumps({"status": "success", "countryCode": "de", "city": " Berlin ", "as": "15169 Google LLC", "isp": "Example ISP"}).encode())
    calls = _patch_get(monkeypatch, response)

    result = ip_api.lookup(_request(timeout=7))

    args, kwargs = calls[0]
    assert args[0] == "http://ip-api.com/json/203.0.113.7"
    assert kwargs["params"] == {"fields": "status,countryCode,city,as,isp,message"}
    assert kwargs["timeout"] == 7
    assert kwargs["stream"] is True
    assert (result.country, result.city, result.asn, result.isp) == ("de", " Berlin ", "15169 Google LLC", "Example ISP")
    assert response.closed is True


def test_ip_api_reports_provider_failure_without_the_response_body(monkeypatch):
    response = FakeResponse(json.dumps({"status": "fail", "message": "reserved range", "city": "Berlin"}).encode())
    _patch_get(monkeypatch, response)

    with pytest.raises(GeoIPProviderError) as failure:
        ip_api.lookup(_request())

    assert str(failure.value) == "reserved range"
    assert "Berlin" not in str(failure.value)
    assert response.closed is True


def test_ip_api_rejects_non_object_and_invalid_json(monkeypatch):
    listed = FakeResponse(b"[]")
    _patch_get(monkeypatch, listed)
    with pytest.raises(GeoIPProviderError, match="invalid response"):
        ip_api.lookup(_request())
    assert listed.closed is True

    broken = FakeResponse(b"{not json")
    _patch_get(monkeypatch, broken)
    with pytest.raises(ResponseBodyError, match="invalid JSON"):
        ip_api.lookup(_request())
    assert broken.closed is True


def test_ip_api_closes_the_response_on_http_error_and_oversize(monkeypatch):
    failing = FakeResponse(b"{}", http_error=RuntimeError("503 Server Error"))
    _patch_get(monkeypatch, failing)
    with pytest.raises(RuntimeError, match="503 Server Error"):
        ip_api.lookup(_request())
    assert failing.closed is True

    oversized = FakeResponse(b"{}", {"Content-Length": str(ip_api.GEOIP_RESPONSE_MAX_BYTES + 1)})
    _patch_get(monkeypatch, oversized)
    with pytest.raises(ResponseBodyError, match="too large"):
        ip_api.lookup(_request())
    assert oversized.closed is True


def test_service_normalizes_provider_values_and_rejects_unknown_providers(monkeypatch, db_session):
    response = FakeResponse(json.dumps({"status": "success", "countryCode": "de", "city": " Berlin " + "x" * 300, "as": "15169 Google LLC", "isp": "y" * 300}).encode())
    _patch_get(monkeypatch, response)

    country, city, asn, isp = geoip_service._lookup_provider_geoip(db_session, "ip-api", "203.0.113.7")

    assert (country, asn) == ("DE", "AS15169")
    assert city is not None and city.startswith("Berlin") and len(city) == 255
    assert isp is not None and len(isp) == 255

    with pytest.raises(ValueError, match="Unsupported GeoIP provider: made-up"):
        geoip_service._lookup_provider_geoip(db_session, "made-up", "203.0.113.7")


def test_seeding_keeps_geoip_disabled_with_the_ip_api_provider(db_session):
    plugin_module = import_plugin_module("geoip", "plugin")
    manager = PluginManager(Path("/not-used"))
    manager.plugins = {"geoip": plugin_module.Plugin()}

    manager.seed_database(db_session)
    db_session.commit()

    settings = {row.key: row.value for row in db_session.query(Setting).filter(Setting.key.like("plugin.geoip.%")).all()}
    assert settings["plugin.geoip.enabled"] == "false"
    assert settings["plugin.geoip.provider"] == "ip-api"
    assert not [key for key in settings if "iplocate" in key]
