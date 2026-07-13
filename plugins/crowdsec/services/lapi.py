from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import requests

from app.core.time import utc_now

logger = logging.getLogger(__name__)

LAPI_TIMEOUT_SECONDS = 10
# Origin recorded on decisions created through OpenSecDash. A distinct origin
# keeps manual OpenSecDash bans identifiable in CrowdSec's own tooling;
# bouncers apply decisions regardless of origin.
DECISION_ORIGIN = "opensecdash"


class LapiError(RuntimeError):
    """Raised for any LAPI communication/authentication failure.

    The message is written into the plugin diagnostics, so it should always
    say enough for a user to act on (unreachable vs. wrong credentials).
    """


def validate_lapi_url(url: str) -> str:
    """Validate and normalize a CrowdSec LAPI base URL."""
    value = str(url or "").strip()
    if not value:
        raise LapiError("LAPI URL must be configured")
    if any(character.isspace() for character in value):
        raise LapiError("LAPI URL must not contain whitespace")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise LapiError(f"Invalid LAPI URL: {exc}") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise LapiError("LAPI URL must use http or https")
    if not parsed.hostname:
        raise LapiError("LAPI URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise LapiError("LAPI URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise LapiError("LAPI URL must not contain a query or fragment")
    if port is not None and not 1 <= port <= 65535:
        raise LapiError("LAPI URL port must be between 1 and 65535")
    return value.rstrip("/")


def _reject_redirect(response: requests.Response, operation: str) -> None:
    if 300 <= response.status_code < 400:
        raise LapiError(f"CrowdSec LAPI {operation} refused an HTTP redirect")


def lapi_login(url: str, machine_id: str, password: str) -> str:
    """Authenticate as a LAPI machine (watcher) and return the JWT."""
    if not url or not machine_id or not password:
        raise LapiError("LAPI URL, login and password must be configured (see the CrowdSec plugin setup guide)")
    base_url = validate_lapi_url(url)
    try:
        response = requests.post(
            f"{base_url}/v1/watchers/login",
            json={"machine_id": machine_id, "password": password},
            timeout=LAPI_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise LapiError(f"CrowdSec LAPI not reachable at {url}: {exc}") from exc
    _reject_redirect(response, "login")
    if response.status_code == 403:
        raise LapiError("CrowdSec LAPI rejected the credentials (machine not registered or wrong password)")
    if response.status_code >= 400:
        raise LapiError(f"CrowdSec LAPI login failed with HTTP {response.status_code}: {response.text[:200]}")
    token = str((response.json() or {}).get("token") or "")
    if not token:
        raise LapiError("CrowdSec LAPI login returned no token")
    return token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def lapi_active_ban_decisions(url: str, token: str) -> list[dict[str, Any]]:
    """Return active ban decisions, flattened from the alerts endpoint.

    Machines (unlike bouncers) read decisions through their alerts. Each
    returned dict carries the fields sync_crowdsec_decisions stores.
    """
    base_url = validate_lapi_url(url)
    try:
        response = requests.get(
            f"{base_url}/v1/alerts",
            params={"has_active_decision": "true", "include_capi": "false", "limit": "500"},
            headers=_auth_headers(token),
            timeout=LAPI_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        _reject_redirect(response, "alerts query")
        response.raise_for_status()
        alerts = response.json() or []
    except requests.RequestException as exc:
        raise LapiError(f"CrowdSec LAPI alerts query failed: {exc}") from exc

    decisions: list[dict[str, Any]] = []
    for alert in alerts if isinstance(alerts, list) else []:
        if not isinstance(alert, dict):
            continue
        for decision in alert.get("decisions") or []:
            if not isinstance(decision, dict):
                continue
            if str(decision.get("type") or "").lower() != "ban":
                continue
            decisions.append(
                {
                    "id": decision.get("id"),
                    "value": decision.get("value"),
                    "scope": decision.get("scope"),
                    "type": str(decision.get("type") or "").lower(),
                    "origin": decision.get("origin"),
                    "scenario": decision.get("scenario") or alert.get("scenario"),
                    "reason": alert.get("scenario"),
                    "duration": decision.get("duration"),
                    "until": decision.get("until"),
                    "raw": decision,
                }
            )
    return decisions


def lapi_add_ban(url: str, token: str, ip: str, duration: str, reason: str) -> None:
    """Create a manual CrowdSec ban decision through LAPI."""
    now = utc_now()
    scenario = f"manual 'ban' from '{DECISION_ORIGIN}' ({reason})" if reason else f"manual 'ban' from '{DECISION_ORIGIN}'"
    scope = "Range" if "/" in ip else "Ip"
    alert = {
        "capacity": 0,
        "decisions": [
            {
                "duration": duration,
                "origin": DECISION_ORIGIN,
                "scenario": scenario,
                "scope": scope,
                "type": "ban",
                "value": ip,
            }
        ],
        "events": [],
        "events_count": 1,
        "labels": None,
        "leakspeed": "0",
        "message": scenario,
        "scenario": scenario,
        "scenario_hash": "",
        "scenario_version": "",
        "simulated": False,
        "source": {"scope": scope, "value": ip},
        "start_at": now.isoformat(),
        "stop_at": (now + timedelta(minutes=1)).isoformat(),
    }
    base_url = validate_lapi_url(url)
    try:
        response = requests.post(
            f"{base_url}/v1/alerts",
            json=[alert],
            headers=_auth_headers(token),
            timeout=LAPI_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        _reject_redirect(response, "ban")
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LapiError(f"CrowdSec LAPI ban failed: {exc}") from exc
    logger.info("Created CrowdSec ban via LAPI for %s (%s)", ip, duration)


def lapi_delete_decision(url: str, token: str, decision_id: str) -> None:
    base_url = validate_lapi_url(url)
    try:
        response = requests.delete(
            f"{base_url}/v1/decisions/{decision_id}",
            headers=_auth_headers(token),
            timeout=LAPI_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        _reject_redirect(response, "unban")
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LapiError(f"CrowdSec LAPI unban failed: {exc}") from exc
    logger.info("Deleted CrowdSec decision %s via LAPI", decision_id)
