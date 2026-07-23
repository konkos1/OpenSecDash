"""Configuration and discovery checks for the single generic OIDC provider."""
from __future__ import annotations

import hashlib
import json
import ssl
import time
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import httpx
from authlib.integrations.starlette_client import OAuth, StarletteOAuth2App
from sqlalchemy.orm import Session

from app.core.remote_urls import RemoteURLPolicyError, validate_remote_url
from app.core.template_context import get_setting_value, get_setting_values
from app.models.users import ExternalIdentity, User
from app.services.auth import (
    active_admin_count,
    active_oidc_admin_count,
    find_user_external_identity,
    normalize_auth_hostname,
)

OIDC_ENABLED_SETTING = "auth.oidc.enabled"
OIDC_DISCOVERY_URL_SETTING = "auth.oidc.discovery_url"
OIDC_CLIENT_ID_SETTING = "auth.oidc.client_id"
OIDC_CLIENT_SECRET_SETTING = "auth.oidc.client_secret"
OIDC_JIT_ENABLED_SETTING = "auth.oidc.jit_enabled"
PASSWORD_LOGIN_ENABLED_SETTING = "auth.password_login_enabled"

# Derived state written by the provider check, never entered by an admin. The
# issuer is stored because it is the login key and because changing it while
# password sign-in is off would lock every administrator out.
OIDC_ISSUER_SETTING = "auth.oidc.issuer"
OIDC_CHECK_STATUS_SETTING = "auth.oidc.check_status"
OIDC_CHECK_AT_SETTING = "auth.oidc.check_at"
OIDC_CHECK_ERROR_SETTING = "auth.oidc.check_error"

CHECK_STATUS_PENDING = "pending"
CHECK_STATUS_HEALTHY = "healthy"
CHECK_STATUS_ERROR = "error"

OIDC_CLIENT_NAME = "oidc"
OIDC_SCOPE = "openid profile email"
CALLBACK_PATH = "/auth/oidc/callback"

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 5.0
MAX_METADATA_BYTES = 256 * 1024
MAX_REDIRECTS = 3
METADATA_CACHE_SECONDS = 300

# Providers and OpenSecDash are expected to keep reasonably accurate time. One
# minute absorbs normal NTP drift without meaningfully widening the window in
# which an expired ID token is still accepted.
CLOCK_SKEW_SECONDS = 60

# An ID token subject is the login key, so it is length-limited to the column
# it is stored in rather than to whatever the provider happens to send.
MAX_SUBJECT_LENGTH = 255

# Only asymmetric signatures: a symmetric ID token signature would be verifiable
# with the client secret alone, and "none" is not a signature at all.
SAFE_ID_TOKEN_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "ES256K", "EdDSA"}
)

REQUIRED_METADATA_ENDPOINTS = ("authorization_endpoint", "token_endpoint", "jwks_uri")
OPTIONAL_METADATA_ENDPOINTS = ("userinfo_endpoint", "end_session_endpoint")


class OidcConfigurationError(Exception):
    """A provider configuration or discovery problem with a stable, safe code.

    Only the code is ever shown or logged: provider responses may contain
    secrets, claims or internal endpoints and never belong in the UI, the
    diagnostics page or the debug report.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class OidcConfig:
    enabled: bool
    discovery_url: str
    client_id: str
    client_secret: str
    jit_enabled: bool
    issuer: str

    @property
    def complete(self) -> bool:
        """Return whether every value needed for an authorization code flow is stored."""
        return bool(self.discovery_url and self.client_id and self.client_secret)


@dataclass
class _CachedMetadata:
    fingerprint: str
    expires_at: float
    metadata: dict[str, Any]


_metadata_cache: _CachedMetadata | None = None


def load_config(db: Session) -> OidcConfig:
    """Read the stored provider configuration, with the client secret decrypted."""
    values = get_setting_values(
        db,
        {
            OIDC_ENABLED_SETTING: "false",
            OIDC_DISCOVERY_URL_SETTING: "",
            OIDC_CLIENT_ID_SETTING: "",
            OIDC_CLIENT_SECRET_SETTING: "",
            OIDC_JIT_ENABLED_SETTING: "false",
            OIDC_ISSUER_SETTING: "",
        },
    )
    return OidcConfig(
        enabled=values[OIDC_ENABLED_SETTING] == "true",
        discovery_url=values[OIDC_DISCOVERY_URL_SETTING],
        client_id=values[OIDC_CLIENT_ID_SETTING],
        client_secret=values[OIDC_CLIENT_SECRET_SETTING],
        jit_enabled=values[OIDC_JIT_ENABLED_SETTING] == "true",
        issuer=values[OIDC_ISSUER_SETTING],
    )


def password_login_enabled(db: Session) -> bool:
    """Return the stored value of the local username and password sign-in switch."""
    return get_setting_value(db, PASSWORD_LOGIN_ENABLED_SETTING, "true") == "true"


def oidc_login_available(config: OidcConfig) -> bool:
    """Return whether the stored provider can actually carry a sign-in."""
    return config.enabled and config.complete and bool(config.issuer)


def effective_password_login_enabled(db: Session) -> bool:
    """Return whether username and password sign-in may be offered right now.

    Every page, route and check asks this helper instead of reading the setting
    itself: a stored "off" only counts while a usable provider exists, so the
    application can never end up with authentication on and no way in.
    """
    if not oidc_login_available(load_config(db)):
        return True
    return password_login_enabled(db)


def admin_reachability_error(
    db: Session,
    user: User,
    *,
    role: str | None = None,
    is_active: bool | None = None,
    keeps_identity: bool = True,
    deleted: bool = False,
) -> str | None:
    """Return an error code when a change would leave no administrator able to sign in.

    This is the single check behind role changes, activation, deletion and
    identity revocation, so none of them can be bypassed by a template that
    happens to hide a button.
    """
    new_role = user.role if role is None else role
    new_active = False if deleted else (user.is_active if is_active is None else is_active)
    was_admin = user.role == "admin" and user.is_active
    stays_admin = new_role == "admin" and new_active
    if was_admin and not stays_admin and active_admin_count(db, exclude_user_id=user.id) == 0:
        return "last_admin"
    if effective_password_login_enabled(db):
        return None
    # Without password sign-in the only way back in is a provider account that
    # belongs to the issuer currently configured.
    config = load_config(db)
    identity = find_user_external_identity(db, user.id)
    reachable_now = was_admin and identity is not None and identity.issuer == config.issuer
    reachable_after = stays_admin and keeps_identity and identity is not None and identity.issuer == config.issuer
    if reachable_now and not reachable_after and active_oidc_admin_count(db, config.issuer, exclude_user_id=user.id) == 0:
        return "last_oidc_admin"
    return None


def config_fingerprint(config: OidcConfig) -> str:
    """Return a non-reversible cache key for one exact provider configuration."""
    material = "\x00".join([config.discovery_url, config.client_id, config.client_secret, config.issuer])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def callback_url(hostname: str) -> str | None:
    """Build the fixed callback URL from the validated authentication hostname."""
    normalized_hostname = normalize_auth_hostname(hostname)
    if normalized_hostname is None:
        return None
    return f"https://{normalized_hostname}{CALLBACK_PATH}"


def validate_provider_url(url: str, *, resolve: bool = True) -> str:
    """Validate a provider URL: HTTPS only, no credentials, query or fragment."""
    value = str(url or "").strip()
    if not value:
        raise OidcConfigurationError("invalid_url")
    try:
        parsed = urlsplit(value)
        _port = parsed.port
    except ValueError as exc:
        raise OidcConfigurationError("invalid_url") from exc
    if parsed.scheme.lower() != "https":
        raise OidcConfigurationError("insecure_url")
    if parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None:
        raise OidcConfigurationError("url_not_plain")
    try:
        return validate_remote_url(value, resolve=resolve)
    except RemoteURLPolicyError as exc:
        raise OidcConfigurationError("blocked_url") from exc


def _ssl_context() -> ssl.SSLContext:
    """Return the TLS context used for every connection to the provider.

    Without this, HTTPX verifies against its bundled certifi list, which can
    never contain a homelab CA. The default context uses the container's own
    trust store instead - including a CA an administrator added there or
    pointed to with SSL_CERT_FILE/SSL_CERT_DIR - while ``trust_env=False``
    keeps proxy environment variables out of provider requests.
    """
    return ssl.create_default_context()


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=READ_TIMEOUT_SECONDS,
        write=READ_TIMEOUT_SECONDS,
        pool=CONNECT_TIMEOUT_SECONDS,
    )


async def _read_limited(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_METADATA_BYTES:
            raise OidcConfigurationError("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_metadata(payload: bytes) -> dict[str, Any]:
    try:
        document = json.loads(payload)
    except ValueError as exc:
        raise OidcConfigurationError("invalid_response") from exc
    if not isinstance(document, dict):
        raise OidcConfigurationError("invalid_response")
    return document


async def fetch_discovery_document(
    discovery_url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolve: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Load a discovery document, re-checking every redirect target against the policy."""
    current = discovery_url
    async with httpx.AsyncClient(
        timeout=_timeout(),
        follow_redirects=False,
        transport=transport,
        trust_env=False,
        verify=_ssl_context(),
    ) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                current = validate_provider_url(current, resolve=resolve)
            except OidcConfigurationError as exc:
                # A redirect target that fails the policy is a redirect
                # problem, not a wrong value in the stored configuration.
                if redirect_count:
                    raise OidcConfigurationError("blocked_redirect") from exc
                raise
            redirect_target: str | None = None
            payload = b""
            try:
                async with client.stream("GET", current, headers={"Accept": "application/json"}) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location", "")
                        if not location:
                            raise OidcConfigurationError("blocked_redirect")
                        redirect_target = urljoin(current, location)
                    else:
                        if response.status_code != 200:
                            raise OidcConfigurationError("invalid_response")
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        if content_type not in ("application/json", "application/jwk-set+json", "text/json"):
                            raise OidcConfigurationError("invalid_response")
                        payload = await _read_limited(response)
            except httpx.HTTPError as exc:
                raise OidcConfigurationError("unreachable") from exc
            if redirect_target is None:
                return current, _parse_metadata(payload)
            current = redirect_target
    raise OidcConfigurationError("blocked_redirect")


def _normalized_issuer(document: dict[str, Any], discovery_url: str) -> str:
    issuer = document.get("issuer")
    if not isinstance(issuer, str) or not issuer.strip():
        raise OidcConfigurationError("invalid_metadata")
    issuer = issuer.strip()
    try:
        # The issuer is compared to the ID token "iss" exactly, so it is only
        # normalized by trimming - never by rewriting or prefix matching.
        parsed_issuer = urlsplit(issuer)
        _issuer_port = parsed_issuer.port
    except ValueError as exc:
        raise OidcConfigurationError("invalid_issuer") from exc
    if (
        parsed_issuer.scheme.lower() != "https"
        or not parsed_issuer.hostname
        or parsed_issuer.query
        or parsed_issuer.fragment
        or parsed_issuer.username is not None
        or parsed_issuer.password is not None
    ):
        raise OidcConfigurationError("invalid_issuer")
    if parsed_issuer.hostname.rstrip(".").lower() != str(urlsplit(discovery_url).hostname or "").rstrip(".").lower():
        raise OidcConfigurationError("invalid_issuer")
    return issuer


def validate_discovery_metadata(document: dict[str, Any], discovery_url: str, *, resolve: bool = True) -> str:
    """Validate a discovery document and return its issuer."""
    issuer = _normalized_issuer(document, discovery_url)
    for key in REQUIRED_METADATA_ENDPOINTS:
        value = document.get(key)
        if not isinstance(value, str) or not value.strip():
            raise OidcConfigurationError("invalid_metadata")
        try:
            validate_provider_url(value, resolve=resolve)
        except OidcConfigurationError as exc:
            raise OidcConfigurationError("blocked_endpoint") from exc
    for key in OPTIONAL_METADATA_ENDPOINTS:
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            try:
                validate_provider_url(value, resolve=resolve)
            except OidcConfigurationError as exc:
                raise OidcConfigurationError("blocked_endpoint") from exc

    response_types = document.get("response_types_supported")
    if not isinstance(response_types, list) or not any(
        isinstance(item, str) and "code" in item.split() for item in response_types
    ):
        raise OidcConfigurationError("unsupported_flow")

    algorithms = document.get("id_token_signing_alg_values_supported")
    if not isinstance(algorithms, list) or not any(
        isinstance(item, str) and item in SAFE_ID_TOKEN_ALGORITHMS for item in algorithms
    ):
        raise OidcConfigurationError("unsupported_algorithms")
    return issuer


async def check_provider(
    discovery_url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolve: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Load and validate a provider's discovery document, returning issuer and metadata."""
    final_url, document = await fetch_discovery_document(discovery_url, transport=transport, resolve=resolve)
    issuer = validate_discovery_metadata(document, final_url, resolve=resolve)
    return issuer, document


def invalidate_provider_cache() -> None:
    """Drop cached provider metadata so a settings change takes effect at once."""
    global _metadata_cache
    _metadata_cache = None


def store_provider_metadata(config: OidcConfig, metadata: dict[str, Any]) -> None:
    """Cache validated metadata for one exact configuration for a short time."""
    global _metadata_cache
    _metadata_cache = _CachedMetadata(
        fingerprint=config_fingerprint(config),
        expires_at=time.monotonic() + METADATA_CACHE_SECONDS,
        metadata=metadata,
    )


def cached_provider_metadata(config: OidcConfig) -> dict[str, Any] | None:
    """Return cached metadata for this configuration, if it is still valid."""
    cached = _metadata_cache
    if cached is None or cached.fingerprint != config_fingerprint(config) or cached.expires_at <= time.monotonic():
        return None
    return cached.metadata


async def load_provider_metadata(
    config: OidcConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolve: bool = True,
) -> dict[str, Any]:
    """Return validated provider metadata, fetching it only when the cache is cold."""
    if not config.complete:
        raise OidcConfigurationError("incomplete_config")
    cached = cached_provider_metadata(config)
    if cached is not None:
        return cached
    issuer, metadata = await check_provider(config.discovery_url, transport=transport, resolve=resolve)
    if config.issuer and issuer != config.issuer:
        raise OidcConfigurationError("invalid_issuer")
    store_provider_metadata(config, metadata)
    return metadata


def safe_id_token_algorithms(metadata: dict[str, Any]) -> list[str]:
    """Return only the advertised ID token algorithms OpenSecDash accepts.

    Authlib verifies the ID token against exactly this list, so filtering here
    is what actually rejects ``none`` and symmetric signatures - a provider may
    advertise a safe algorithm next to unsafe ones. An empty result falls back
    to RS256, which fails closed: a token signed with anything else is refused.
    """
    advertised = metadata.get("id_token_signing_alg_values_supported")
    if not isinstance(advertised, list):
        return ["RS256"]
    return [item for item in advertised if isinstance(item, str) and item in SAFE_ID_TOKEN_ALGORITHMS] or ["RS256"]


def id_token_claims_options(config: OidcConfig) -> dict[str, dict[str, Any]]:
    """Return the required ID token claim conditions for the stored provider.

    Issuer and audience are pinned explicitly: Authlib only checks the audience
    indirectly through ``azp``, which would still accept a token minted for a
    different client of the same provider.
    """
    return {
        "iss": {"essential": True, "values": [config.issuer]},
        "aud": {"essential": True, "values": [config.client_id]},
    }


def build_oauth_client(
    config: OidcConfig,
    metadata: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> StarletteOAuth2App:
    """Build an Authlib client from already validated metadata.

    The metadata is passed in instead of a ``server_metadata_url`` so Authlib
    never fetches discovery itself: that fetch would follow redirects without
    running them through the provider URL policy again.
    """
    client_kwargs: dict[str, Any] = {
        "scope": OIDC_SCOPE,
        "code_challenge_method": "S256",
        "timeout": _timeout(),
        "follow_redirects": False,
        "trust_env": False,
        "verify": _ssl_context(),
    }
    if transport is not None:
        client_kwargs["transport"] = transport
    oauth = OAuth()
    oauth.register(
        name=OIDC_CLIENT_NAME,
        client_id=config.client_id,
        client_secret=config.client_secret,
        issuer=metadata["issuer"],
        authorization_endpoint=metadata["authorization_endpoint"],
        token_endpoint=metadata["token_endpoint"],
        jwks_uri=metadata["jwks_uri"],
        userinfo_endpoint=metadata.get("userinfo_endpoint"),
        id_token_signing_alg_values_supported=safe_id_token_algorithms(metadata),
        client_kwargs=client_kwargs,
    )
    return cast(StarletteOAuth2App, oauth.create_client(OIDC_CLIENT_NAME))


def provider_diagnostics(db: Session, hostname: str) -> dict[str, Any]:
    """Return sanitized OIDC rows for the authentication diagnostics section.

    Reads stored state only: the diagnostics page must never turn into a
    scheduled request against the provider.
    """
    config = load_config(db)
    check_status = get_setting_value(db, OIDC_CHECK_STATUS_SETTING, CHECK_STATUS_PENDING)
    if check_status not in (CHECK_STATUS_PENDING, CHECK_STATUS_HEALTHY, CHECK_STATUS_ERROR):
        check_status = CHECK_STATUS_PENDING
    expected_callback = callback_url(hostname)
    checks = [
        ("configuration", CHECK_STATUS_HEALTHY if config.complete else CHECK_STATUS_ERROR),
        ("discovery", check_status),
        ("callback", CHECK_STATUS_HEALTHY if expected_callback is not None else CHECK_STATUS_ERROR),
    ]
    # Sign-in method switches are reported as plain on/off, not as health: an
    # intentionally disabled option is not a problem to fix.
    flags = [
        ("password_login", "on" if effective_password_login_enabled(db) else "off"),
        ("jit", "on" if config.jit_enabled else "off"),
    ]
    return {
        "enabled": config.enabled,
        "identities": db.query(ExternalIdentity).count(),
        "rows": [{"key": key, "status": status, "message_key": f"diagnostics.oidc.{key}.{status}"} for key, status in checks],
        "flags": [{"key": key, "value": value, "value_key": f"diagnostics.oidc.flag.{value}"} for key, value in flags],
    }
