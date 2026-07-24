"""Normalize proxy headers from explicitly trusted upstream peers."""

import ipaddress
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

logger = logging.getLogger(__name__)

TRUSTED_PROXIES_ENV = "OSD_TRUSTED_PROXIES"
PROXY_STATE_EXPLICITLY_TRUSTED = "osd_proxy_explicitly_trusted"
PROXY_STATE_CLIENT_ADDRESS = "osd_proxy_client_address"
PROXY_STATE_PEER_ADDRESS = "osd_proxy_peer_address"
PROXY_STATE_FORWARDED_PROTO = "osd_proxy_forwarded_proto"
PROXY_STATE_FORWARDED_HOST = "osd_proxy_forwarded_host"
PROXY_STATE_FORWARDED_PORT = "osd_proxy_forwarded_port"

DEFAULT_TRUSTED_NETWORKS: tuple[str, ...] = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)

TrustedNetwork: TypeAlias = ipaddress.IPv4Network | ipaddress.IPv6Network
TrustedProxies: TypeAlias = list[TrustedNetwork] | None
ASGIApp: TypeAlias = Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]


class _ReadFromEnvironment:
    pass


_READ_FROM_ENV = _ReadFromEnvironment()
_FORWARDED_HEADERS = {b"x-forwarded-for", b"x-forwarded-proto", b"x-forwarded-host", b"x-forwarded-port"}


def parse_trusted_proxies(raw: str | None) -> TrustedProxies:
    """Parse OSD_TRUSTED_PROXIES; None in the result means trust every peer."""

    values = DEFAULT_TRUSTED_NETWORKS if raw is None else raw.split(",")
    if raw is not None and not raw.strip():
        return []
    if raw is not None and raw.strip() == "*":
        return None

    networks: list[TrustedNetwork] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError:
            logger.warning("Ignoring invalid trusted proxy network: %s", value.strip())
            continue
        networks.append(network)
    return networks


def _is_trusted_address(host: str, trusted_proxies: TrustedProxies) -> bool:
    if trusted_proxies is None:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...] = (address,)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        addresses += (address.ipv4_mapped,)
    return any(address in network for address in addresses for network in trusted_proxies)


def _throttle_client_address(client: Any, trusted_proxies: TrustedProxies) -> str | None:
    """Resolved downstream client address safe to key a per-source bucket on.

    Returns the normalized downstream client host only when it is a distinct
    client. A trusted reverse proxy (or a missing peer) is never returned:
    using the shared proxy address as a bucket would let a single client fill
    it and lock out everyone behind that proxy.

    This is the fine-grained bucket and it depends on X-Forwarded-For. When the
    trusted-proxy range is configured too broadly a request can rotate this
    identity; the rotation-proof peer address from :func:`_peer_address` is the
    backstop for that case.
    """
    if not (isinstance(client, tuple) and client and isinstance(client[0], str)):
        return None
    host = client[0]
    if _is_trusted_address(host, trusted_proxies):
        return None
    return host


def _peer_address(client: Any) -> str | None:
    """Immediate TCP peer host, captured before X-Forwarded-For is applied.

    Unlike the resolved downstream client this can never be rotated through a
    request header, so it still bounds forwarded-address spraying when the
    trusted-proxy range is configured too broadly. The peer of a trusted proxy
    is deliberately kept: it feeds a separate, higher-threshold bucket that
    tolerates normal shared-proxy traffic while capping sustained abuse.
    """
    if not (isinstance(client, tuple) and client and isinstance(client[0], str)):
        return None
    return client[0]


def _first_forwarded_header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for header_name, value in headers:
        if header_name.lower() == name:
            return value.decode("latin-1")
    return None


def _client_from_x_forwarded_for(value: str, trusted_proxies: TrustedProxies) -> str | None:
    entries = [entry.strip() for entry in value.split(",")]
    try:
        addresses = [ipaddress.ip_address(entry) for entry in entries]
    except ValueError:
        return None
    if not addresses:
        return None

    for address in reversed(addresses):
        if not _is_trusted_address(str(address), trusted_proxies):
            return str(address)
    return str(addresses[0])


class ProxyHeadersMiddleware:
    """Apply X-Forwarded-* headers only from trusted reverse proxies."""

    def __init__(self, app: ASGIApp, trusted_proxies: TrustedProxies | _ReadFromEnvironment = _READ_FROM_ENV) -> None:
        self.app = app
        if isinstance(trusted_proxies, _ReadFromEnvironment):
            raw = os.environ.get(TRUSTED_PROXIES_ENV)
            self.trusted_proxies = parse_trusted_proxies(raw)
            self.explicit_trust = raw is not None and raw.strip() not in ("", "*")
        else:
            self.trusted_proxies = trusted_proxies
            self.explicit_trust = trusted_proxies is not None and bool(trusted_proxies)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = list(scope.get("headers", []))
        state = scope.setdefault("state", {})
        state[PROXY_STATE_EXPLICITLY_TRUSTED] = False
        state[PROXY_STATE_CLIENT_ADDRESS] = None
        state[PROXY_STATE_PEER_ADDRESS] = None
        state[PROXY_STATE_FORWARDED_PROTO] = None
        state[PROXY_STATE_FORWARDED_HOST] = None
        state[PROXY_STATE_FORWARDED_PORT] = None
        client = scope.get("client")
        # Preserve the immediate TCP peer before X-Forwarded-For rewrites
        # scope["client"]; it keys the rotation-proof throttling bucket.
        state[PROXY_STATE_PEER_ADDRESS] = _peer_address(client)
        trusted = isinstance(client, tuple) and bool(client) and _is_trusted_address(client[0], self.trusted_proxies)
        if not trusted:
            scope["headers"] = [(name, value) for name, value in headers if name.lower() not in _FORWARDED_HEADERS]
            state[PROXY_STATE_CLIENT_ADDRESS] = _throttle_client_address(scope.get("client"), self.trusted_proxies)
            await self.app(scope, receive, send)
            return

        state[PROXY_STATE_EXPLICITLY_TRUSTED] = self.explicit_trust
        forwarded_for = _first_forwarded_header(headers, b"x-forwarded-for")
        if forwarded_for is not None:
            forwarded_client = _client_from_x_forwarded_for(forwarded_for, self.trusted_proxies)
            if forwarded_client is not None:
                scope["client"] = (forwarded_client, 0)

        forwarded_proto = _first_forwarded_header(headers, b"x-forwarded-proto")
        if forwarded_proto is not None:
            proto = forwarded_proto.split(",", 1)[0].strip().lower()
            if proto in {"http", "https"}:
                state[PROXY_STATE_FORWARDED_PROTO] = proto
                scope["scheme"] = {"http": "ws", "https": "wss"}[proto] if scope["type"] == "websocket" else proto

        forwarded_host = _first_forwarded_header(headers, b"x-forwarded-host")
        if forwarded_host is not None:
            host = forwarded_host.split(",", 1)[0].strip()
            if host:
                state[PROXY_STATE_FORWARDED_HOST] = host
                scope["headers"] = [(name, value) for name, value in headers if name.lower() != b"host"] + [(b"host", host.encode("latin-1"))]

        forwarded_port = _first_forwarded_header(headers, b"x-forwarded-port")
        if forwarded_port is not None:
            port = forwarded_port.split(",", 1)[0].strip()
            if port.isdigit():
                state[PROXY_STATE_FORWARDED_PORT] = port

        state[PROXY_STATE_CLIENT_ADDRESS] = _throttle_client_address(scope.get("client"), self.trusted_proxies)
        await self.app(scope, receive, send)
