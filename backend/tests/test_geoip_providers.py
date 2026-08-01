import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from conftest import import_plugin_module

from app.core.http_responses import ResponseBodyError
from app.models.settings import Setting
from app.plugins.base import EnrichmentPlugin
from app.plugins.manager import PluginManager

geoip_plugin = import_plugin_module("geoip", "plugin")
geoip_package = import_plugin_module("geoip", "services")
geoip_service = import_plugin_module("geoip", "services.geoip")
providers = import_plugin_module("geoip", "services.providers")
provider_base = import_plugin_module("geoip", "services.providers.base")
ip_api = import_plugin_module("geoip", "services.providers.ip_api")
iplocate = import_plugin_module("geoip", "services.providers.iplocate")

PROVIDERS = providers.PROVIDERS
get_provider = providers.get_provider
GeoIPLookupRequest = provider_base.GeoIPLookupRequest
GeoIPConfigurationError = provider_base.GeoIPConfigurationError
GeoIPProviderError = provider_base.GeoIPProviderError

SERVICE_SOURCE = Path(cast(str, geoip_service.__file__)).read_text(encoding="utf-8")
DUMMY_KEY = "dummy-iplocate-key"


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        headers: dict[str, str] | None = None,
        http_error: Exception | None = None,
        status_code: int = 200,
    ):
        self.body = body
        self.headers = headers or {}
        self.http_error = http_error
        self.status_code = status_code
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
    return GeoIPLookupRequest(
        ip=overrides.get("ip", "203.0.113.7"),
        timeout=overrides.get("timeout", 3),
        settings=overrides.get("settings", {}),
    )


def _iplocate_request(**overrides) -> GeoIPLookupRequest:
    return _request(settings={iplocate.API_KEY_SETTING: DUMMY_KEY}, **overrides)


def _patch_get(monkeypatch, response: FakeResponse, module=ip_api) -> list[tuple[tuple, dict]]:
    calls: list[tuple[tuple, dict]] = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(module.requests, "get", fake_get)
    return calls


def test_registry_resolves_only_the_statically_registered_providers():
    assert sorted(PROVIDERS) == ["ip-api", "iplocate"]
    assert get_provider("ip-api") is ip_api.PROVIDER
    assert get_provider("iplocate") is iplocate.PROVIDER
    # One file per provider, so neither endpoint contract can grow into the other.
    assert Path(cast(str, ip_api.__file__)).name == "ip_api.py"
    assert Path(cast(str, iplocate.__file__)).name == "iplocate.py"
    assert "plugins/geoip/services/providers" in Path(cast(str, iplocate.__file__)).as_posix()

    with pytest.raises(ValueError, match="Unsupported GeoIP provider: made-up"):
        get_provider("made-up")


def test_geoip_plugin_owns_the_service_and_uses_the_enrichment_hook():
    assert isinstance(geoip_plugin.Plugin(), EnrichmentPlugin)
    assert "plugins/geoip/services" in Path(cast(str, geoip_service.__file__)).as_posix()


def test_plugin_geoip_services_are_exported_from_the_package():
    for name in (
        "cleanup_expired_cache",
        "enrich_event_values",
        "enrich_pending_events",
        "geoip_enabled",
        "lookup_country",
        "lookup_geoip",
        "normalize_asn",
        "normalize_city",
        "normalize_isp",
        "normalize_lookup_target",
    ):
        assert hasattr(geoip_package, name)


def test_service_holds_no_provider_url_or_wire_field_names():
    # The provider boundary only pays off if endpoint details cannot leak back
    # into the shared orchestration.
    for needle in (
        "ip-api.com",
        "http://",
        "countryCode",
        '"as"',
        "status,countryCode",
        "iplocate.io",
        "https://",
        "X-API-Key",
        "country_code",
        "company",
    ):
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


def test_iplocate_sends_the_fixed_eu_request_with_a_header_key(monkeypatch):
    response = FakeResponse(json.dumps({"country_code": "de"}).encode())
    calls = _patch_get(monkeypatch, response, iplocate)

    iplocate.lookup(_iplocate_request(timeout=9))

    args, kwargs = calls[0]
    assert args[0] == "https://eu-api.iplocate.io/api/lookup/203.0.113.7"
    assert kwargs["params"] == {
        "include": "country_code,city,asn.asn,asn.name,company.name,hosting.provider"
    }
    assert kwargs["headers"] == {"X-API-Key": DUMMY_KEY}
    assert kwargs["timeout"] == 9
    assert kwargs["stream"] is True
    assert kwargs["allow_redirects"] is False
    assert kwargs["verify"] is True
    # The key must never end up anywhere in the URL or the query string.
    assert DUMMY_KEY not in args[0]
    assert DUMMY_KEY not in str(kwargs["params"])
    assert response.closed is True


def test_iplocate_needs_a_key_before_any_request(monkeypatch):
    monkeypatch.setattr(iplocate.requests, "get", lambda *args, **kwargs: pytest.fail("unexpected IPLocate request"))

    for settings in ({}, {iplocate.API_KEY_SETTING: "   "}):
        with pytest.raises(GeoIPConfigurationError, match="API key is not configured"):
            iplocate.lookup(_request(settings=settings))


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (
            iplocate.requests.exceptions.InvalidHeader(
                f"Invalid header value containing {DUMMY_KEY}"
            ),
            "request could not be sent",
        ),
        (
            iplocate.requests.exceptions.ConnectionError(
                "failed for https://eu-api.iplocate.io/api/lookup/203.0.113.7"
            ),
            "request could not be sent",
        ),
    ],
)
def test_iplocate_normalizes_request_failures_without_key_or_url(monkeypatch, failure, message):
    def fail_request(*args, **kwargs):
        raise failure

    monkeypatch.setattr(iplocate.requests, "get", fail_request)

    with pytest.raises(GeoIPProviderError) as raised:
        iplocate.lookup(_iplocate_request())

    text = str(raised.value)
    assert message in text
    assert DUMMY_KEY not in text
    assert "203.0.113.7" not in text
    assert "eu-api.iplocate.io" not in text


def test_iplocate_normalizes_stream_failures_and_closes_response(monkeypatch):
    class BrokenResponse(FakeResponse):
        def iter_content(self, chunk_size):
            raise iplocate.requests.exceptions.ConnectionError(
                "failed for https://eu-api.iplocate.io/api/lookup/203.0.113.7"
            )

    response = BrokenResponse(b"")
    _patch_get(monkeypatch, response, iplocate)

    with pytest.raises(GeoIPProviderError) as raised:
        iplocate.lookup(_iplocate_request())

    assert str(raised.value) == "IPLocate response could not be read"
    assert response.closed is True


def test_iplocate_fills_all_four_fields_from_a_valid_answer(monkeypatch):
    payload = {
        "country_code": " de ",
        "city": " Berlin ",
        "asn": {"asn": "AS15169", "name": "Google LLC"},
        "company": {"name": "Example Company"},
        "hosting": {"provider": "Example Hosting"},
        "latitude": 52.5,
        "threat": {"is_tor": True},
    }
    _patch_get(monkeypatch, FakeResponse(json.dumps(payload).encode()), iplocate)

    result = iplocate.lookup(_iplocate_request())

    assert (result.country, result.city, result.asn, result.isp) == (" de ", " Berlin ", "AS15169", "Example Company")


@pytest.mark.parametrize(
    ("payload", "expected_isp"),
    [
        ({"company": {"name": "  "}, "hosting": {"provider": "Example Hosting"}, "asn": {"name": "Example ASN"}}, "Example Hosting"),
        ({"company": None, "hosting": {}, "asn": {"name": "Example ASN"}}, "Example ASN"),
        ({"company": "not-an-object", "hosting": ["nope"], "asn": 5}, None),
        ({}, None),
    ],
)
def test_iplocate_isp_falls_back_and_survives_unexpected_shapes(monkeypatch, payload, expected_isp):
    _patch_get(monkeypatch, FakeResponse(json.dumps(payload).encode()), iplocate)

    result = iplocate.lookup(_iplocate_request())

    assert result.isp == expected_isp
    assert (result.country, result.city) == (None, None)


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (401, "rejected the API key"),
        (403, "rejected the API key"),
        (429, "quota or rate limit reached"),
        (302, "unexpected redirect"),
        (503, "lookup failed"),
    ],
)
def test_iplocate_reports_http_failures_without_key_body_or_url(monkeypatch, status_code, message):
    response = FakeResponse(b'{"country_code":"DE","secret":"leak"}', status_code=status_code)
    _patch_get(monkeypatch, response, iplocate)

    with pytest.raises(GeoIPProviderError) as failure:
        iplocate.lookup(_iplocate_request())

    text = str(failure.value)
    assert message in text
    assert DUMMY_KEY not in text
    assert "leak" not in text
    assert "203.0.113.7" not in text
    assert "eu-api.iplocate.io" not in text
    assert response.closed is True


def test_iplocate_rejects_non_object_invalid_and_oversized_json(monkeypatch):
    listed = FakeResponse(b"[]")
    _patch_get(monkeypatch, listed, iplocate)
    with pytest.raises(GeoIPProviderError, match="invalid response"):
        iplocate.lookup(_iplocate_request())
    assert listed.closed is True

    broken = FakeResponse(b"{not json")
    _patch_get(monkeypatch, broken, iplocate)
    with pytest.raises(ResponseBodyError, match="invalid JSON"):
        iplocate.lookup(_iplocate_request())
    assert broken.closed is True

    oversized = FakeResponse(b"{}", {"Content-Length": str(iplocate.GEOIP_RESPONSE_MAX_BYTES + 1)})
    _patch_get(monkeypatch, oversized, iplocate)
    with pytest.raises(ResponseBodyError, match="too large"):
        iplocate.lookup(_iplocate_request())
    assert oversized.closed is True

    streamed = FakeResponse(b'{"city":"' + b"x" * (iplocate.GEOIP_RESPONSE_MAX_BYTES + 1) + b'"}')
    _patch_get(monkeypatch, streamed, iplocate)
    with pytest.raises(ResponseBodyError, match="too large"):
        iplocate.lookup(_iplocate_request())
    assert streamed.closed is True


def test_service_normalizes_iplocate_values(monkeypatch, db_session):
    payload = {"country_code": " de ", "city": " Berlin ", "asn": {"asn": "15169"}, "company": {"name": " Example Company "}}
    _patch_get(monkeypatch, FakeResponse(json.dumps(payload).encode()), iplocate)
    db_session.add(Setting(key="plugin.geoip.iplocate_api_key", value=DUMMY_KEY))
    db_session.commit()

    assert geoip_service._lookup_provider_geoip(db_session, "iplocate", "203.0.113.7") == (
        "DE",
        "Berlin",
        "AS15169",
        "Example Company",
    )


def test_service_normalizes_provider_values_and_rejects_unknown_providers(monkeypatch, db_session):
    response = FakeResponse(json.dumps({"status": "success", "countryCode": "de", "city": " Berlin " + "x" * 300, "as": "15169 Google LLC", "isp": "y" * 300}).encode())
    _patch_get(monkeypatch, response)

    country, city, asn, isp = geoip_service._lookup_provider_geoip(db_session, "ip-api", "203.0.113.7")

    assert (country, asn) == ("DE", "AS15169")
    assert city is not None and city.startswith("Berlin") and len(city) == 255
    assert isp is not None and len(isp) == 255

    with pytest.raises(ValueError, match="Unsupported GeoIP provider: made-up"):
        geoip_service._lookup_provider_geoip(db_session, "made-up", "203.0.113.7")


def _seed_geoip(db_session) -> dict[str, str]:
    plugin_module = import_plugin_module("geoip", "plugin")
    manager = PluginManager(Path("/not-used"))
    manager.plugins = {"geoip": plugin_module.Plugin()}

    manager.seed_database(db_session)
    db_session.commit()

    return {row.key: row.value for row in db_session.query(Setting).filter(Setting.key.like("plugin.geoip.%")).all()}


def test_seeding_a_fresh_installation_preselects_iplocate_and_stays_disabled(monkeypatch, db_session):
    for module in (ip_api, iplocate):
        monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: pytest.fail("unexpected GeoIP request"))

    settings = _seed_geoip(db_session)

    assert settings["plugin.geoip.enabled"] == "false"
    assert settings["plugin.geoip.provider"] == "iplocate"
    assert settings["plugin.geoip.iplocate_api_key"] == ""


def test_seeding_never_overwrites_an_existing_provider_choice(db_session):
    db_session.add_all(
        [
            Setting(key="plugin.geoip.enabled", value="true"),
            Setting(key="plugin.geoip.provider", value="ip-api"),
        ]
    )
    db_session.commit()

    settings = _seed_geoip(db_session)

    assert settings["plugin.geoip.enabled"] == "true"
    assert settings["plugin.geoip.provider"] == "ip-api"


def _health(**settings) -> dict[str, str]:
    plugin_module = import_plugin_module("geoip", "plugin")
    context = SimpleNamespace(get=lambda key, default="": settings.get(key, default))
    return asyncio.run(plugin_module.Plugin().health(cast(Any, context)))


def test_health_describes_the_transport_without_probing_or_leaking_the_key(monkeypatch):
    for module in (ip_api, iplocate):
        monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: pytest.fail("unexpected GeoIP request"))

    missing_key = _health(provider="iplocate", iplocate_api_key="  ")
    configured = _health(provider="iplocate", iplocate_api_key=DUMMY_KEY)
    legacy = _health(provider="ip-api")
    unknown = _health(provider="made-up")

    assert missing_key["status"] == "error"
    assert "no API key is configured" in missing_key["message"]
    assert configured["status"] == "warning"
    assert "EU endpoint over encrypted HTTPS" in configured["message"]
    assert DUMMY_KEY not in configured["message"]
    assert legacy == {
        "status": "warning",
        "message": "GeoIP is active: uncached public IPs are sent to ip-api.com over unencrypted HTTP.",
    }
    assert unknown == {"status": "error", "message": "Unsupported GeoIP provider: made-up"}
