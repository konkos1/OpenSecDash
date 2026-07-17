import asyncio
from typing import Any

from starlette.requests import Request

from app.web.auth import auth_transport_diagnostics
from app.web.proxy_headers import (
    DEFAULT_TRUSTED_NETWORKS,
    PROXY_STATE_EXPLICITLY_TRUSTED,
    PROXY_STATE_FORWARDED_HOST,
    PROXY_STATE_FORWARDED_PORT,
    PROXY_STATE_FORWARDED_PROTO,
    PROXY_STATE_PEER_ADDRESS,
    ProxyHeadersMiddleware,
    parse_trusted_proxies,
)


async def _recording_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    return None


def _scope(*, client=("10.0.0.2", 4242), headers=None, scope_type="http", scheme="http") -> dict[str, Any]:
    return {
        "type": scope_type,
        "client": client,
        "headers": headers or [(b"host", b"internal.example")],
        "scheme": scheme,
        "method": "GET",
        "path": "/",
        "query_string": b"",
    }


async def _receive() -> dict[str, Any]:
    return {"type": "http.request"}


async def _send(message: dict[str, Any]) -> None:
    return None


def _apply(scope: dict[str, Any], trusted_proxies: Any = ...) -> None:
    middleware = (
        ProxyHeadersMiddleware(_recording_app, parse_trusted_proxies(None))
        if trusted_proxies is ...
        else ProxyHeadersMiddleware(_recording_app, trusted_proxies)
    )
    asyncio.run(middleware(scope, _receive, _send))


def _header_names(scope: dict[str, Any]) -> set[bytes]:
    return {name.lower() for name, _value in scope["headers"]}


def test_parse_trusted_proxies_supports_all_configuration_forms(caplog):
    defaults = parse_trusted_proxies(None)

    assert defaults is not None
    assert [str(network) for network in defaults] == list(DEFAULT_TRUSTED_NETWORKS)
    assert parse_trusted_proxies("") == []
    assert parse_trusted_proxies("  ") == []
    assert parse_trusted_proxies("*") is None
    configured_networks = parse_trusted_proxies("192.168.1.10, 10.0.0.0/8")
    valid_networks = parse_trusted_proxies("192.168.1.10, quatsch")
    assert configured_networks is not None
    assert valid_networks is not None
    assert [str(network) for network in configured_networks] == ["192.168.1.10/32", "10.0.0.0/8"]
    assert [str(network) for network in valid_networks] == ["192.168.1.10/32"]
    assert "Ignoring invalid trusted proxy network: quatsch" in caplog.messages


def test_x_forwarded_for_sets_the_client_ip_for_a_trusted_peer():
    scope = _scope(headers=[(b"host", b"internal.example"), (b"X-Forwarded-For", b"203.0.113.9")])

    _apply(scope)

    assert scope["client"] == ("203.0.113.9", 0)
    assert scope["state"][PROXY_STATE_PEER_ADDRESS] == "10.0.0.2"
    request_client = Request(scope).client
    assert request_client is not None
    assert request_client.host == "203.0.113.9"


def test_x_forwarded_for_ignores_spoofed_leftmost_entries():
    scope = _scope(headers=[(b"X-Forwarded-For", b"6.6.6.6, 203.0.113.9, 10.0.0.3")])

    _apply(scope)

    assert scope["client"] == ("203.0.113.9", 0)


def test_x_forwarded_for_uses_leftmost_entry_when_every_entry_is_trusted():
    scope = _scope(headers=[(b"X-Forwarded-For", b"192.168.1.5")])

    _apply(scope)

    assert scope["client"] == ("192.168.1.5", 0)


def test_untrusted_peers_and_missing_clients_fail_closed():
    headers = [
        (b"host", b"internal.example"),
        (b"X-Forwarded-For", b"203.0.113.9"),
        (b"X-Forwarded-Proto", b"https"),
        (b"X-Forwarded-Host", b"osd.example.com"),
        (b"X-Forwarded-Port", b"443"),
    ]
    public_scope = _scope(client=("203.0.113.50", 4242), headers=headers)
    missing_client_scope = _scope(client=None, headers=headers)

    _apply(public_scope)
    _apply(missing_client_scope)

    for scope, client in ((public_scope, ("203.0.113.50", 4242)), (missing_client_scope, None)):
        assert scope["client"] == client
        assert scope["state"][PROXY_STATE_PEER_ADDRESS] == (client[0] if client is not None else None)
        assert scope["scheme"] == "http"
        assert dict(scope["headers"])[b"host"] == b"internal.example"
        assert not _header_names(scope) & {b"x-forwarded-for", b"x-forwarded-proto", b"x-forwarded-host", b"x-forwarded-port"}


def test_invalid_x_forwarded_for_does_not_block_other_valid_headers():
    scope = _scope(headers=[(b"X-Forwarded-For", b"not-an-ip"), (b"X-Forwarded-Proto", b"https")])

    _apply(scope)

    assert scope["client"] == ("10.0.0.2", 4242)
    assert scope["scheme"] == "https"


def test_x_forwarded_proto_handles_http_https_and_websockets():
    https_scope = _scope(headers=[(b"X-Forwarded-Proto", b"HTTPS")])
    invalid_scope = _scope(headers=[(b"X-Forwarded-Proto", b"gopher")])
    websocket_scope = _scope(scope_type="websocket", scheme="ws", headers=[(b"X-Forwarded-Proto", b"https")])

    _apply(https_scope)
    _apply(invalid_scope)
    _apply(websocket_scope)

    assert https_scope["scheme"] == "https"
    assert invalid_scope["scheme"] == "http"
    assert websocket_scope["scheme"] == "wss"


def test_x_forwarded_host_replaces_host_header():
    scope = _scope(headers=[(b"host", b"internal.example"), (b"X-Forwarded-Host", b"osd.example.com")])

    _apply(scope)

    assert dict(scope["headers"])[b"host"] == b"osd.example.com"
    assert Request(scope).url.hostname == "osd.example.com"


def test_auth_proxy_provenance_requires_explicit_non_wildcard_configuration(monkeypatch):
    headers = [
        (b"host", b"internal.example"),
        (b"X-Forwarded-Proto", b"https"),
        (b"X-Forwarded-Host", b"osd.example.com"),
        (b"X-Forwarded-Port", b"443"),
    ]
    default_scope = _scope(headers=headers)
    wildcard_scope = _scope(headers=headers)
    configured_scope = _scope(headers=headers)

    monkeypatch.delenv("OSD_TRUSTED_PROXIES", raising=False)
    asyncio.run(ProxyHeadersMiddleware(_recording_app)(default_scope, _receive, _send))
    monkeypatch.setenv("OSD_TRUSTED_PROXIES", "*")
    asyncio.run(ProxyHeadersMiddleware(_recording_app)(wildcard_scope, _receive, _send))
    monkeypatch.setenv("OSD_TRUSTED_PROXIES", "10.0.0.2")
    asyncio.run(ProxyHeadersMiddleware(_recording_app)(configured_scope, _receive, _send))

    assert default_scope["state"][PROXY_STATE_EXPLICITLY_TRUSTED] is False
    assert wildcard_scope["state"][PROXY_STATE_EXPLICITLY_TRUSTED] is False
    assert configured_scope["state"][PROXY_STATE_EXPLICITLY_TRUSTED] is True
    assert configured_scope["state"][PROXY_STATE_FORWARDED_PROTO] == "https"
    assert configured_scope["state"][PROXY_STATE_FORWARDED_HOST] == "osd.example.com"
    assert configured_scope["state"][PROXY_STATE_FORWARDED_PORT] == "443"
    transport = auth_transport_diagnostics(Request(configured_scope), "osd.example.com")
    assert transport["status"] == "healthy"
    assert {row["key"]: row["status"] for row in transport["rows"]} == {
        "proxy_configuration": "healthy",
        "proxy_peer": "healthy",
        "https": "healthy",
        "port": "healthy",
        "forwarded_host": "healthy",
        "hostname": "healthy",
    }


def test_empty_and_trust_all_configuration_control_header_processing():
    headers = [(b"X-Forwarded-For", b"203.0.113.9")]
    disabled_scope = _scope(headers=headers)
    trust_all_scope = _scope(client=("203.0.113.50", 4242), headers=headers)

    _apply(disabled_scope, [])
    _apply(trust_all_scope, parse_trusted_proxies("*"))

    assert disabled_scope["client"] == ("10.0.0.2", 4242)
    assert b"x-forwarded-for" not in _header_names(disabled_scope)
    assert trust_all_scope["client"] == ("203.0.113.9", 0)


def test_ipv4_mapped_ipv6_proxy_peer_is_trusted():
    scope = _scope(client=("::ffff:10.0.0.2", 4242), headers=[(b"X-Forwarded-For", b"203.0.113.9")])

    _apply(scope)

    assert scope["client"] == ("203.0.113.9", 0)


def test_lifespan_scope_passes_through_unchanged():
    scope = {"type": "lifespan", "headers": [(b"X-Forwarded-For", b"203.0.113.9")]}

    _apply(scope)

    assert scope == {"type": "lifespan", "headers": [(b"X-Forwarded-For", b"203.0.113.9")]}
