from __future__ import annotations

import ipaddress
import socket
from urllib.parse import SplitResult, urlsplit


class RemoteURLPolicyError(ValueError):
    """Raised when an outbound read target violates the shared URL policy."""


_BLOCKED_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}
_BLOCKED_METADATA_HOSTS = {
    "instance-data.ec2.internal",
    "metadata.azure.internal",
    "metadata.google.internal",
}


def _parse_remote_url(url: str) -> SplitResult:
    value = str(url or "").strip()
    if not value or any(character.isspace() for character in value):
        raise RemoteURLPolicyError("Remote URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise RemoteURLPolicyError("Remote URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise RemoteURLPolicyError("Remote URL must use http or https")
    if not parsed.hostname:
        raise RemoteURLPolicyError("Remote URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise RemoteURLPolicyError("Remote URL must not contain credentials")
    if port is not None and not 1 <= port <= 65535:
        raise RemoteURLPolicyError("Remote URL port must be between 1 and 65535")
    return parsed


def _validate_address(address_text: str) -> None:
    try:
        address = ipaddress.ip_address(address_text.split("%", 1)[0])
    except ValueError as exc:
        raise RemoteURLPolicyError("Remote host resolved to an invalid address") from exc
    if (
        address in _BLOCKED_METADATA_ADDRESSES
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        raise RemoteURLPolicyError("Remote host resolves to a blocked address")


def validate_remote_url(url: str, *, resolve: bool = True) -> str:
    """Validate a server-side HTTP read target while allowing private LANs."""
    parsed = _parse_remote_url(url)
    hostname = str(parsed.hostname).rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname in _BLOCKED_METADATA_HOSTS:
        raise RemoteURLPolicyError("Remote host is blocked")

    try:
        _validate_address(hostname)
        is_ip_literal = True
    except RemoteURLPolicyError:
        try:
            ipaddress.ip_address(hostname.split("%", 1)[0])
        except ValueError:
            is_ip_literal = False
        else:
            raise

    if resolve and not is_ip_literal:
        try:
            addresses = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise RemoteURLPolicyError("Remote host could not be resolved") from exc
        if not addresses:
            raise RemoteURLPolicyError("Remote host could not be resolved")
        for address in addresses:
            _validate_address(str(address[4][0]))
    return parsed.geturl()


def validate_credentialed_http_url(url: str) -> str:
    """Validate a credentialed HTTP API base URL without resolving it yet."""
    parsed = _parse_remote_url(url)
    if parsed.query or parsed.fragment:
        raise RemoteURLPolicyError("API URL must not contain a query or fragment")
    return parsed.geturl().rstrip("/")
