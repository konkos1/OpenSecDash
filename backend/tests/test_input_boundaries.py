from __future__ import annotations

import asyncio
import gzip
import io
import socket
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.events import EventCreate, router as events_router
from app.core.input_limits import (
    MAX_ASSET_APPS_PER_SYSTEM,
    MAX_ASSET_FIELD_LENGTH,
    MAX_EVENT_RAW_DATA_LENGTH,
    MAX_JSON_DEPTH,
)
from app.core.remote_urls import RemoteURLPolicyError, validate_remote_url
from app.services.geoip import enrich_event_values
from app.database.dependencies import get_db
from app.web.body_limit import RequestBodyLimitMiddleware
from conftest import import_plugin_module

importer = import_plugin_module("json_assets", "services.importer")
source_module = import_plugin_module("json_assets", "services.source")
mqtt_module = import_plugin_module("mqtt", "plugin")
proxmox_module = import_plugin_module("proxmox_assets", "services.sync")


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/assets.json",
        "ftp://inventory.example.test/assets.json",
        "http://user:password@inventory.example.test/assets.json",
        "http:///assets.json",
        "http://localhost/assets.json",
        "http://127.0.0.1/assets.json",
        "http://[::1]/assets.json",
        "http://169.254.10.20/assets.json",
        "http://169.254.169.254/latest/meta-data",
        "http://100.100.100.200/latest/meta-data",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[fe80::1]/assets.json",
    ],
)
def test_remote_url_policy_rejects_unsafe_targets(url):
    with pytest.raises(RemoteURLPolicyError):
        validate_remote_url(url, resolve=False)


def test_remote_url_policy_allows_private_lan_targets():
    assert validate_remote_url("http://192.168.10.20:8080/assets.json") == "http://192.168.10.20:8080/assets.json"
    assert validate_remote_url("http://[fd12:3456::20]/assets.json") == "http://[fd12:3456::20]/assets.json"


def test_remote_url_policy_rejects_dns_answer_to_link_local(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))],
    )

    with pytest.raises(RemoteURLPolicyError, match="blocked address"):
        validate_remote_url("http://inventory.example.test/assets.json")


class _RawBody:
    def __init__(self, body: bytes):
        self._body = io.BytesIO(body)

    def read(self, amount: int, decode_content: bool = False) -> bytes:
        return self._body.read(amount)


class _Response:
    def __init__(self, status: int, body: bytes = b"{}", headers: dict[str, str] | None = None):
        self.status_code = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.raw = _RawBody(body)

    def close(self):
        pass


class _Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.trust_env = True
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)

    def close(self):
        pass


def test_json_asset_redirect_revalidates_target(monkeypatch):
    session = _Session([_Response(302, headers={"Location": "http://169.254.169.254/latest"})])
    monkeypatch.setattr(source_module.requests, "Session", lambda: session)

    with pytest.raises(source_module.AssetSourceError, match="fetched safely"):
        source_module.load_asset_source("url", "http://192.168.1.20/assets.json")

    assert session.trust_env is False
    assert session.calls[0][1]["allow_redirects"] is False


def test_json_asset_redirect_chain_is_limited(monkeypatch):
    session = _Session([_Response(302, headers={"Location": "/next"}) for _ in range(4)])
    monkeypatch.setattr(source_module.requests, "Session", lambda: session)

    with pytest.raises(source_module.AssetSourceError, match="redirect limit"):
        source_module.load_asset_source("url", "http://192.168.1.20/assets.json")


def test_json_asset_compression_cannot_bypass_unpacked_limit(monkeypatch):
    compressed = gzip.compress(b"x" * (source_module.MAX_ASSET_INVENTORY_BYTES + 1))
    session = _Session([_Response(200, compressed, {"Content-Type": "application/json", "Content-Encoding": "gzip"})])
    monkeypatch.setattr(source_module.requests, "Session", lambda: session)

    with pytest.raises(source_module.AssetSourceError, match="unpacked limit"):
        source_module.load_asset_source("url", "http://192.168.1.20/assets.json")


def test_json_asset_size_limit_with_and_without_content_length(monkeypatch):
    monkeypatch.setattr(source_module, "MAX_ASSET_INVENTORY_BYTES", 16)
    declared_session = _Session([_Response(200, b"{}", {"Content-Type": "application/json", "Content-Length": "17"})])
    monkeypatch.setattr(source_module.requests, "Session", lambda: declared_session)
    with pytest.raises(source_module.AssetSourceError, match="10 MiB limit"):
        source_module.load_asset_source("url", "http://192.168.1.20/assets.json")

    streamed_session = _Session([_Response(200, b"{" + b"x" * 16)])
    monkeypatch.setattr(source_module.requests, "Session", lambda: streamed_session)
    with pytest.raises(source_module.AssetSourceError, match="compressed response"):
        source_module.load_asset_source("url", "http://192.168.1.20/assets.json")


def test_json_asset_errors_do_not_expose_query_or_response_body(monkeypatch):
    session = _Session([_Response(500, b"upstream-secret")])
    monkeypatch.setattr(source_module.requests, "Session", lambda: session)

    with pytest.raises(source_module.AssetSourceError) as exc_info:
        source_module.load_asset_source("url", "http://192.168.1.20/assets.json?token=query-secret")

    message = str(exc_info.value)
    assert "query-secret" not in message
    assert "upstream-secret" not in message


def test_asset_inventory_limits_apply_before_database_changes(db_session):
    inventory = {"systems": [{"vmid": "1", "hostname": "host", "apps": [{"name": "a"}] * (MAX_ASSET_APPS_PER_SYSTEM + 1)}]}

    with pytest.raises(ValueError, match="apps per system"):
        importer.import_json_assets(db_session, inventory)
    assert not db_session.new

    with pytest.raises(ValueError, match="field exceeds"):
        importer.import_json_assets(db_session, {"systems": [{"vmid": "1", "hostname": "x" * (MAX_ASSET_FIELD_LENGTH + 1)}]})


def test_event_model_accepts_boundaries_and_rejects_boundary_plus_one():
    EventCreate(event_type="x" * 50, source="x" * 100, raw_data="x" * MAX_EVENT_RAW_DATA_LENGTH)

    with pytest.raises(ValidationError):
        EventCreate(event_type="x" * 51)
    with pytest.raises(ValidationError):
        EventCreate(event_type="test", raw_data="x" * (MAX_EVENT_RAW_DATA_LENGTH + 1))
    with pytest.raises(ValidationError, match="1 MiB limit"):
        EventCreate(event_type="test", data_json={"value": "x" * (1024 * 1024)})

    nested: object = "leaf"
    for _ in range(MAX_JSON_DEPTH):
        nested = {"value": nested}
    with pytest.raises(ValidationError, match="maximum JSON depth"):
        EventCreate(event_type="test", data_json=nested)  # type: ignore[arg-type] - deliberately invalid depth


def test_event_api_enforces_field_and_query_boundaries(db_session):
    local_app = FastAPI()
    local_app.include_router(events_router)

    def override_db():
        yield db_session

    local_app.dependency_overrides[get_db] = override_db
    with TestClient(local_app) as client:
        rejected_field = client.post("/api/events", json={"event_type": "x" * 51})
        rejected_query = client.get("/api/events", params={"path": "x" * 2049})

    assert rejected_field.status_code == 422
    assert rejected_query.status_code == 422


def test_geoip_default_disabled_makes_no_network_call(monkeypatch, db_session):
    monkeypatch.setattr("app.services.geoip.requests.get", lambda *args, **kwargs: pytest.fail("unexpected GeoIP request"))
    values = {"ip": "8.8.8.8"}

    enrich_event_values(db_session, values)

    assert values == {"ip": "8.8.8.8"}


def test_body_limit_checks_stream_without_content_length():
    local_app = FastAPI()
    local_app.add_middleware(RequestBodyLimitMiddleware, max_bytes=4)

    @local_app.post("/api/write")
    async def write(request: Request):
        return {"size": len(await request.body())}

    with TestClient(local_app) as client:
        response = client.post("/api/write", content=iter([b"123", b"45"]))

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large"}


def test_body_limit_rejects_oversized_content_length_before_endpoint():
    local_app = FastAPI()
    local_app.add_middleware(RequestBodyLimitMiddleware, max_bytes=4)
    called = False

    @local_app.post("/write")
    async def write(request: Request):
        nonlocal called
        called = True
        return {"size": len(await request.body())}

    with TestClient(local_app) as client:
        response = client.post("/write", content=b"12345")

    assert response.status_code == 413
    assert called is False
    assert "The submitted data exceeds the allowed size." in response.text


def test_proxmox_client_rejects_redirect_with_credentials(monkeypatch):
    session = _Session([_Response(302, headers={"Location": "https://elsewhere.example.test"})])
    monkeypatch.setattr(proxmox_module.requests, "Session", lambda: session)
    client = proxmox_module.ProxmoxClient("https://pve.example.test:8006", "token-id", "token-secret")

    with pytest.raises(proxmox_module.ProxmoxConnectionError, match="refused an HTTP redirect"):
        client.get("/nodes")

    assert session.trust_env is False
    assert session.calls[0][1]["allow_redirects"] is False


def test_mqtt_tls_publish_requires_certificate_verification(monkeypatch):
    calls = []
    publish = SimpleNamespace(multiple=lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(mqtt_module.importlib, "import_module", lambda name: publish)
    context = SimpleNamespace(
        get=lambda key, default="": {
            "host": "mqtt.example.test",
            "port": "8883",
            "tls_mode": "tls",
            "ca_file": "/certs/homelab-ca.pem",
        }.get(key, default)
    )

    mqtt_module.Plugin().publish_many(context, [("test/topic", "payload", True)])

    tls = calls[0][1]["tls"]
    assert tls["ca_certs"] == "/certs/homelab-ca.pem"
    assert tls["cert_reqs"] is not None
    assert tls["tls_version"] == mqtt_module.ssl.PROTOCOL_TLS_CLIENT


def test_mqtt_tls_health_checks_certificate_and_hostname(monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    wrapped = Connection()
    tls_context = SimpleNamespace(
        wrap_socket=lambda connection, server_hostname: (
            wrapped
            if connection is raw_connection and server_hostname == "mqtt.example.test"
            else pytest.fail("TLS health check did not verify the configured hostname")
        )
    )
    raw_connection = Connection()
    monkeypatch.setattr(mqtt_module.socket, "create_connection", lambda *args, **kwargs: raw_connection)
    create_context_calls = []
    monkeypatch.setattr(
        mqtt_module.ssl,
        "create_default_context",
        lambda *, cafile=None: create_context_calls.append(cafile) or tls_context,
    )
    context = SimpleNamespace(
        get=lambda key, default="": {
            "host": "mqtt.example.test",
            "port": "8883",
            "tls_mode": "tls",
            "ca_file": "/certs/homelab-ca.pem",
        }.get(key, default)
    )

    result = asyncio.run(mqtt_module.Plugin().health(cast(Any, context)))

    assert result["status"] == "healthy"
    assert create_context_calls == ["/certs/homelab-ca.pem"]
