from __future__ import annotations

import ipaddress
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import as_utc, utc_now
from app.models.core import CrowdSecDecision, Diagnostic

logger = logging.getLogger(__name__)

LAPI_COMPONENT = "lapi"
DECISION_SYNC_INTERVAL_SECONDS = 120


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = as_utc(parsed).replace(tzinfo=None)
    return parsed


def _decision_ip(item: dict[str, Any]) -> str | None:
    scope = str(item.get("scope") or item.get("Scope") or "").lower()
    value = item.get("value") or item.get("Value") or item.get("ip") or item.get("IP")
    if scope in {"ip", "range"} and value:
        return str(value)
    return str(value) if value and ("." in str(value) or ":" in str(value)) else None


def _decision_id(item: dict[str, Any]) -> str | None:
    value = item.get("id") or item.get("ID") or item.get("uuid") or item.get("UUID")
    return str(value) if value is not None and str(value).strip() else None


def _update_decision_diagnostic(db: Session, status: str, message: str | None) -> None:
    diagnostic = db.query(Diagnostic).filter(Diagnostic.plugin == "crowdsec", Diagnostic.component == LAPI_COMPONENT).first()
    if diagnostic is None:
        diagnostic = Diagnostic(plugin="crowdsec", component=LAPI_COMPONENT)
        db.add(diagnostic)
    diagnostic.status = status
    diagnostic.last_error = message
    diagnostic.last_run = utc_now().replace(tzinfo=None)


def crowdsec_lapi_status(db: Session) -> Diagnostic | None:
    return db.query(Diagnostic).filter(Diagnostic.plugin == "crowdsec", Diagnostic.component == LAPI_COMPONENT).first()


def _canonical_decision_target(value: object, scope: object = "") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if str(scope or "").lower() == "range" or "/" in text:
            return str(ipaddress.ip_network(text, strict=False))
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text


def active_decision_for_ip(db: Session, ip: str) -> CrowdSecDecision | None:
    target = _canonical_decision_target(ip)
    decision = (
        db.query(CrowdSecDecision)
        .filter(CrowdSecDecision.ip == target, CrowdSecDecision.decision_type == "ban")
        .order_by(CrowdSecDecision.synced_at.desc())
        .first()
    )
    if decision is not None:
        return decision

    # Rows synchronized before target canonicalization can still use CrowdSec's
    # expanded IPv6 notation until the next successful LAPI refresh.
    candidates = (
        db.query(CrowdSecDecision)
        .filter(CrowdSecDecision.decision_type == "ban")
        .order_by(CrowdSecDecision.synced_at.desc())
        .all()
    )
    return next(
        (
            candidate
            for candidate in candidates
            if _canonical_decision_target(candidate.ip, candidate.scope) == target
        ),
        None,
    )


def active_decision_by_id(db: Session, decision_id: str) -> CrowdSecDecision | None:
    return (
        db.query(CrowdSecDecision)
        .filter(
            CrowdSecDecision.decision_id == str(decision_id),
            CrowdSecDecision.decision_type == "ban",
        )
        .first()
    )


def _fetch_decisions_via_lapi(db: Session) -> tuple[bool, str, list[Any]]:
    from .lapi import LapiError, lapi_active_ban_decisions, lapi_login

    url = get_setting_value(db, "plugin.crowdsec.lapi_url", "http://127.0.0.1:8080")
    login = get_setting_value(db, "plugin.crowdsec.lapi_login", "")
    password = get_setting_value(db, "plugin.crowdsec.lapi_password", "")
    try:
        token = lapi_login(url, login, password)
        return True, "", lapi_active_ban_decisions(url, token)
    except LapiError as exc:
        return False, str(exc), []


def sync_crowdsec_decisions(db: Session, *, force: bool = False) -> tuple[bool, str]:
    latest = db.query(CrowdSecDecision.synced_at).order_by(CrowdSecDecision.synced_at.desc()).first()
    now = utc_now().replace(tzinfo=None)
    if not force and latest and (now - latest[0]).total_seconds() < DECISION_SYNC_INTERVAL_SECONDS:
        return True, "CrowdSec decisions are fresh."

    ok, message, items = _fetch_decisions_via_lapi(db)
    if not ok:
        _update_decision_diagnostic(db, "error", message)
        return False, message

    db.query(CrowdSecDecision).delete()
    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        ip = _decision_ip(item)
        decision_id = _decision_id(item)
        decision_type = str(item.get("type") or item.get("Type") or "").lower() or None
        if not ip or not decision_id or decision_type != "ban":
            continue
        scope = str(item.get("scope") or item.get("Scope") or "")
        db.add(
            CrowdSecDecision(
                decision_id=decision_id,
                ip=_canonical_decision_target(ip, scope),
                scope=scope,
                decision_type=decision_type,
                origin=str(item.get("origin") or item.get("Origin") or "") or None,
                scenario=str(item.get("scenario") or item.get("Scenario") or item.get("reason") or item.get("Reason") or "") or None,
                reason=str(item.get("reason") or item.get("Reason") or "") or None,
                duration=str(item.get("duration") or item.get("Duration") or "") or None,
                until=_parse_datetime(item.get("until") or item.get("Until")),
                raw_json=item.get("raw", item),
                synced_at=now,
            )
        )
        count += 1
    db.flush()
    _update_decision_diagnostic(db, "healthy", f"{count} active CrowdSec ban decision(s) synced.")
    from .policies import reconcile_enforcements_after_sync

    reconcile_enforcements_after_sync(db)
    logger.info("Synced %d active CrowdSec decisions", count)
    return True, f"{count} active CrowdSec ban decision(s) synced."
