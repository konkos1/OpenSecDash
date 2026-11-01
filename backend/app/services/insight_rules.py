from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.core import Diagnostic, Insight, InsightRule as InsightRuleModel
from app.models.events import Event
from app.models.settings import Setting
from app.services.insight_catalog import BUILTIN_INSIGHT_DEFINITIONS
from app.services.notifications import sync_insight_notification_rules
from app.services.insights import MAX_SAMPLE_IDS, ip_correlation_key, record_insight, utc_iso

logger = logging.getLogger(__name__)

RULE_SOURCE_URL = "https://opensecdash.app/rules/insights-rules.json"
RULE_MANIFEST_URL = "https://opensecdash.app/rules/insights-rules-v1.sha256.json"
RULE_REFRESH_INTERVAL = timedelta(hours=24)
RULE_TIMEOUT_SECONDS = 5
RULE_MANIFEST_MAX_BYTES = 8 * 1024
RULESET_MAX_BYTES = 256 * 1024
_READ_CHUNK_BYTES = 32 * 1024
SUPPORTED_SCHEMA_MAJOR = 2
RULE_FETCHED_AT_KEY = "insight_rules.fetched_at"
RULE_VERSION_KEY = "insight_rules.version"
RULE_SOURCE_KEY = "insight_rules.source"
RULE_ETAG_KEY = "insight_rules.etag"
RULE_SHA256_KEY = "insight_rules.sha256"
DEFAULT_RULES_PATH = Path(__file__).resolve().parents[1] / "insights" / "rules" / "default-rules.json"


@dataclass(frozen=True)
class InsightRule:
    id: str
    title: str
    description: str
    level: str
    confidence: float
    event_types: tuple[str, ...]
    path_contains_any: tuple[str, ...]
    group_by: str = "ip"
    window_minutes: int = 5
    threshold: int = 1
    min_distinct_ips: int = 1
    source: str = "bundled"
    schema_version: str = "1"
    ruleset_version: str = ""
    kind: str = "web_probe"
    match_options: dict[str, Any] | None = None


def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row is not None else default


def _save_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def _delete_setting(db: Session, key: str) -> None:
    db.query(Setting).filter(Setting.key == key).delete()


def _update_diagnostic(db: Session, status: str, message: str) -> None:
    row = db.query(Diagnostic).filter(Diagnostic.plugin == "insight_rules", Diagnostic.component == "ruleset").first()
    if row is None:
        row = Diagnostic(plugin="insight_rules", component="ruleset")
        db.add(row)
    row.status = status
    row.last_run = utc_now().replace(tzinfo=None)
    row.last_error = message


def _load_default_ruleset() -> dict[str, Any]:
    return json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))


def _read_capped_content(response: requests.Response, *, max_bytes: int) -> bytes:
    # Stream the body so a faulty or hostile source cannot exhaust the 512 MiB
    # container by returning a huge payload: reject early on a declared
    # Content-Length over the limit, then read block-wise and stop as soon as
    # the accumulated size passes the cap instead of buffering everything first.
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            declared_length = int(declared)
        except ValueError as exc:
            raise ValueError("Remote insight rules returned an invalid Content-Length") from exc
        if declared_length > max_bytes:
            raise ValueError("Remote insight rules response is too large")
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=_READ_CHUNK_BYTES):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("Remote insight rules response is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _response_json(response: requests.Response, *, max_bytes: int) -> dict[str, Any]:
    data = json.loads(_read_capped_content(response, max_bytes=max_bytes))
    if not isinstance(data, dict):
        raise ValueError("Remote insight rules response must be an object")
    return data


def _expected_ruleset_hash(manifest: dict[str, Any]) -> str:
    if manifest.get("schema_version") != 1 or manifest.get("path") != "/rules/insights-rules.json":
        raise ValueError("Invalid insight rules hash manifest")
    digest = manifest.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("Invalid insight rules SHA-256 digest")
    expires = manifest.get("expires")
    if not isinstance(expires, str):
        raise ValueError("Insight rules hash manifest has no expiry")
    try:
        expiry_date = datetime.fromisoformat(expires).date()
    except ValueError as exc:
        raise ValueError("Invalid insight rules hash manifest expiry") from exc
    if expiry_date < utc_now().date():
        raise ValueError(f"Insight rules hash manifest expired on {expires}")
    return digest


def _schema_major(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"Invalid insight rules schema_version: {value}")
    text = str(value)
    if any(not part.isdigit() for part in text.split(".")):
        raise ValueError(f"Invalid insight rules schema_version: {value}")
    major_text = text.split(".", 1)[0]
    if not major_text.isdigit():
        raise ValueError(f"Invalid insight rules schema_version: {value}")
    return int(major_text)


def _match_options(item: dict[str, Any], major: int) -> dict[str, Any] | None:
    if major == 1:
        return None
    kind = item.get("kind", "web_probe")
    if kind not in ("web_probe", "scan_ban_chain"):
        raise ValueError(f"Insight rule {item.get('id')} has an invalid kind")
    options = item.get("match_options", {})
    if not isinstance(options, dict):
        raise ValueError("Insight rule match_options must be an object")
    allowed = {"path_equals_any", "path_prefix_any", "status_codes", "methods"}
    if kind == "scan_ban_chain":
        allowed.update(("min_distinct_assets", "min_distinct_countries", "followup_seconds"))
    if options.keys() - allowed:
        raise ValueError("Insight rule has unknown match_options")
    options = dict(options)
    for name in ("path_equals_any", "path_prefix_any"):
        if name in options:
            paths = options[name]
            if (not isinstance(paths, list) or len(paths) > 50
                    or any(not isinstance(path, str) or not path.strip() or len(path) > 200 for path in paths)):
                raise ValueError(f"Insight rule has invalid {name}")
    if "status_codes" in options:
        codes = options["status_codes"]
        if (not isinstance(codes, list) or not 1 <= len(codes) <= 100
                or any(isinstance(code, bool) or not isinstance(code, int) or not 100 <= code <= 599 for code in codes)
                or len(set(codes)) != len(codes)):
            raise ValueError("Insight rule has invalid status_codes")
    if "methods" in options:
        methods = options["methods"]
        allowed_methods = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"}
        if (not isinstance(methods, list) or not 1 <= len(methods) <= 10
                or any(not isinstance(method, str) or method.upper() not in allowed_methods for method in methods)):
            raise ValueError("Insight rule has invalid methods")
        options["methods"] = [method.upper() for method in methods]
    if kind == "scan_ban_chain":
        for name, default, minimum, maximum in (
            ("min_distinct_assets", 2, 2, 100), ("min_distinct_countries", 2, 1, 100),
            ("followup_seconds", 60, 1, 300),
        ):
            value = options.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"Insight rule has invalid {name}")
            options[name] = value
    return options


def _validate_ruleset(data: dict[str, Any]) -> dict[str, Any]:
    major = _schema_major(data.get("schema_version"))
    if major not in (1, SUPPORTED_SCHEMA_MAJOR):
        raise ValueError(f"Unsupported insight rules schema_version: {data.get('schema_version')}")
    version = data.get("ruleset_version", "")
    if not isinstance(version, str) or len(version) > 20:
        raise ValueError("Insight ruleset has an invalid ruleset_version")
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise ValueError("Insight ruleset must contain a rules list")
    if len(rules) > 200:
        raise ValueError("Insight ruleset contains too many rules")
    seen_ids: set[str] = set()
    chain_count = 0
    for item in rules:
        if not isinstance(item, dict):
            raise ValueError("Insight rule must be an object")
        rule_id = item.get("id")
        title = item.get("title")
        if not rule_id or not title:
            raise ValueError("Insight rule id/title is required")
        if not isinstance(rule_id, str) or not rule_id.strip() or len(rule_id) > 100:
            raise ValueError(f"Insight rule {rule_id} has an invalid id")
        if rule_id in seen_ids:
            raise ValueError(f"Duplicate insight rule id: {rule_id}")
        seen_ids.add(rule_id)
        if not isinstance(title, str) or not title.strip() or len(title) > 255:
            raise ValueError(f"Insight rule {rule_id} has an invalid title")
        description = item.get("description", title)
        if not isinstance(description, str) or len(description) > 2000:
            raise ValueError(f"Insight rule {rule_id} has an invalid description")
        if item.get("level", "medium") not in ("low", "medium", "high", "critical"):
            raise ValueError(f"Insight rule {rule_id} has an invalid level")
        confidence = item.get("confidence", 0.7)
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1 or not math.isfinite(confidence)):
            raise ValueError(f"Insight rule {rule_id} has invalid confidence")
        if item.get("group_by", "ip") not in ("ip", "path"):
            raise ValueError(f"Unsupported insight rule group_by for {item.get('id')}: {item.get('group_by')}")
        options = _match_options(item, major)
        paths = item.get("path_contains_any", [])
        if not isinstance(paths, list) or (major == 1 and not paths):
            raise ValueError(f"Insight rule {item.get('id')} must define path_contains_any")
        if len(paths) > 50 or any(not isinstance(path, str) or not path.strip() or len(path) > 200 for path in paths):
            raise ValueError(f"Insight rule {item.get('id')} has invalid path patterns")
        if major == 2 and not (paths or (options or {}).get("path_equals_any") or (options or {}).get("path_prefix_any")):
            raise ValueError(f"Insight rule {rule_id} must define a path list")
        event_types = item.get("event_types", [])
        if not isinstance(event_types, list) or not event_types:
            raise ValueError(f"Insight rule {item.get('id')} must define event_types")
        if len(event_types) > 50 or any(
            not isinstance(value, str) or not value.strip() or len(value) > 50 for value in event_types
        ):
            raise ValueError(f"Insight rule {rule_id} has invalid event_types")
        threshold = item.get("threshold", 1)
        window_minutes = item.get("window_minutes", 5)
        if isinstance(threshold, bool) or not isinstance(threshold, int):
            raise ValueError(f"Insight rule {rule_id} has invalid threshold")
        if isinstance(window_minutes, bool) or not isinstance(window_minutes, int):
            raise ValueError(f"Insight rule {rule_id} has invalid window_minutes")
        min_distinct_ips_value = item.get("min_distinct_ips", 1)
        if not isinstance(min_distinct_ips_value, int) or isinstance(min_distinct_ips_value, bool):
            raise ValueError(f"Insight rule {item.get('id')} has invalid min_distinct_ips")
        min_distinct_ips = min_distinct_ips_value
        if threshold < 1 or threshold > 100:
            raise ValueError(f"Insight rule {item.get('id')} has invalid threshold")
        if window_minutes < 1 or window_minutes > 1440:
            raise ValueError(f"Insight rule {item.get('id')} has invalid window_minutes")
        if min_distinct_ips < 1 or min_distinct_ips > 1000:
            raise ValueError(f"Insight rule {item.get('id')} has invalid min_distinct_ips")
        if major == 2 and item.get("kind", "web_probe") == "scan_ban_chain":
            chain_count += 1
            if (item.get("group_by", "ip") != "path" or min_distinct_ips < 2 or window_minutes > 60
                    or not set(event_types) <= {"access.denied", "access.error"}):
                raise ValueError(f"Insight rule {rule_id} has invalid scan_ban_chain constraints")
    if chain_count > 20:
        raise ValueError("Insight ruleset contains too many chain rules")
    return data


def parse_rules(data: dict[str, Any], *, source: str = "bundled") -> list[InsightRule]:
    validated = _validate_ruleset(data)
    schema_version = str(validated.get("schema_version"))
    ruleset_version = validated.get("ruleset_version", "")
    rules: list[InsightRule] = []
    for item in validated["rules"]:
        rules.append(
            InsightRule(
                id=item["id"],
                title=item["title"],
                description=item.get("description", item["title"]),
                level=item.get("level", "medium"),
                confidence=float(item.get("confidence", 0.7)),
                event_types=tuple(item["event_types"]),
                path_contains_any=tuple(item.get("path_contains_any", [])),
                kind=item.get("kind", "web_probe") if _schema_major(schema_version) == 2 else "web_probe",
                match_options=_match_options(item, _schema_major(schema_version)),
                group_by=item.get("group_by", "ip"),
                window_minutes=item.get("window_minutes", 5),
                threshold=item.get("threshold", 1),
                min_distinct_ips=item.get("min_distinct_ips", 1),
                source=source,
                schema_version=schema_version,
                ruleset_version=ruleset_version,
            )
        )
    return rules


def _apply_rule(db: Session, rule: InsightRule, existing: InsightRuleModel | None, now: datetime) -> None:
    if existing is None:
        existing = InsightRuleModel(rule_id=rule.id, title=rule.title, created_at=now)
        db.add(existing)
    existing.source = rule.source
    existing.schema_version = rule.schema_version
    existing.ruleset_version = rule.ruleset_version
    existing.title = rule.title
    existing.description = rule.description
    existing.level = rule.level
    existing.confidence = rule.confidence
    existing.group_by = rule.group_by
    existing.window_minutes = rule.window_minutes
    existing.threshold = rule.threshold
    existing.min_distinct_ips = rule.min_distinct_ips
    existing.event_types = list(rule.event_types)
    existing.path_contains_any = list(rule.path_contains_any)
    existing.kind = rule.kind
    existing.match_options = rule.match_options
    existing.is_active = True
    existing.updated_at = now
    existing.last_seen_at = now


def import_ruleset(db: Session, data: dict[str, Any], *, source: str) -> dict[str, Any]:
    rules = parse_rules(data, source=source)
    bundled = {rule.id: rule for rule in parse_rules(_load_default_ruleset())}
    builtin_ids = {definition.type for definition in BUILTIN_INSIGHT_DEFINITIONS}
    imported_rule_ids = {rule.id for rule in rules}
    now = utc_now().replace(tzinfo=None)
    imported = 0
    updated = 0
    with db.begin_nested():
        existing_rules = {row.rule_id: row for row in db.query(InsightRuleModel).all()}
        for rule in rules:
            existing = existing_rules.get(rule.id)
            if source.startswith("plugin:") and (
                rule.id in builtin_ids or rule.id in bundled
                or (existing is not None and existing.source != source)
            ):
                raise ValueError(f"Insight rule {rule.id} conflicts with another source")
            if source in ("bundled", "remote") and existing is not None and existing.source.startswith("plugin:"):
                raise ValueError(f"Insight rule {rule.id} conflicts with another source")
        for rule in rules:
            existing = existing_rules.get(rule.id)
            # Seeding must preserve the last verified remote override even offline.
            if source == "bundled" and existing is not None and existing.source == "remote":
                continue
            if existing is None:
                imported += 1
            else:
                updated += 1
            _apply_rule(db, rule, existing, now)
        for existing in existing_rules.values():
            if existing.source != source or existing.rule_id in imported_rule_ids:
                continue
            if source == "remote" and existing.rule_id in bundled:
                _apply_rule(db, bundled[existing.rule_id], existing, now)
            elif existing.is_active:
                existing.is_active = False
                existing.updated_at = now
        db.flush()
        sync_insight_notification_rules(db)
    invalidate_active_rules_cache(db)
    return {"version": data.get("ruleset_version"), "count": len(rules), "imported": imported, "updated": updated}


def import_bundled_rules(db: Session) -> dict[str, Any]:
    return import_ruleset(db, _load_default_ruleset(), source="bundled")


def invalidate_active_rules_cache(db: Session) -> None:
    """Discard the per-session active-rules cache after rule changes."""
    db.info.pop(_ACTIVE_RULES_CACHE_KEY, None)


def _ruleset_state_message(db: Session) -> str:
    rows = db.query(InsightRuleModel).filter(InsightRuleModel.is_active == True).all()
    bundled_rows = [row for row in rows if row.source == "bundled"]
    remote_rows = [row for row in rows if row.source == "remote"]
    plugin_rows = [row for row in rows if row.source.startswith("plugin:")]
    bundled_versions = sorted({row.ruleset_version for row in bundled_rows if row.ruleset_version})
    remote_versions = sorted({row.ruleset_version for row in remote_rows if row.ruleset_version})
    plugin_sources = sorted({row.source for row in plugin_rows})
    sources = "+".join(
        source
        for source, source_rows in (("bundled", bundled_rows), ("remote", remote_rows), *[(source, [source]) for source in plugin_sources])
        if source_rows
    ) or "none"
    parts = [f"source={sources}"]
    if bundled_rows:
        parts.append(f"bundled_version={','.join(bundled_versions) or 'unknown'}")
        parts.append(f"bundled={len(bundled_rows)}")
    if remote_rows:
        parts.append(f"remote_version={','.join(remote_versions) or 'unknown'}")
        parts.append(f"remote={len(remote_rows)}")
    if plugin_rows:
        parts.append(f"plugin_sources={','.join(plugin_sources)}")
        parts.append(f"plugin={len(plugin_rows)}")
    parts.append(f"active={len(rows)}")
    return "; ".join(parts)


_ACTIVE_RULES_CACHE_KEY = "_opensecdash_active_insight_rules"


def active_rules(db: Session) -> list[InsightRule]:
    # Cached per session: this runs once per stored event (the ingestion hot
    # path), and the rule set only changes via the daily refresh loop or a
    # bundled-rules import - never mid-batch. Sessions are short-lived (one
    # request / one datasource tick), which bounds any staleness to a batch.
    cached = db.info.get(_ACTIVE_RULES_CACHE_KEY)
    if cached is not None:
        return cached
    if db.query(InsightRuleModel).filter(InsightRuleModel.is_active == True).count() == 0:
        import_bundled_rules(db)
        db.flush()
    rows = db.query(InsightRuleModel).filter(InsightRuleModel.is_active == True).order_by(InsightRuleModel.rule_id).all()
    rules = [
        InsightRule(
            id=row.rule_id,
            title=row.title,
            description=row.description,
            level=row.level,
            confidence=row.confidence,
            event_types=tuple(str(value) for value in (row.event_types or [])),
            path_contains_any=tuple(str(value) for value in (row.path_contains_any or [])),
            group_by=row.group_by,
            window_minutes=row.window_minutes,
            threshold=row.threshold,
            min_distinct_ips=row.min_distinct_ips,
            source=row.source,
            schema_version=row.schema_version,
            ruleset_version=row.ruleset_version,
            kind=row.kind,
            match_options=row.match_options,
        )
        for row in rows
    ]
    db.info[_ACTIVE_RULES_CACHE_KEY] = rules
    return rules


def refresh_insight_rules(db: Session, *, force: bool = False) -> dict[str, Any]:
    bundled = import_bundled_rules(db)
    fetched_at_text = _setting(db, RULE_FETCHED_AT_KEY, "")
    if not force and fetched_at_text:
        try:
            fetched_at = datetime.fromisoformat(fetched_at_text)
            if utc_now().replace(tzinfo=None) - fetched_at < RULE_REFRESH_INTERVAL:
                _update_diagnostic(db, "healthy", f"Insights engine rules loaded from database: {_ruleset_state_message(db)}")
                db.commit()
                return {"status": "skipped", "source": "database", "version": _setting(db, RULE_VERSION_KEY, str(bundled["version"])), "count": len(active_rules(db))}
        except ValueError:
            pass

    headers = {}
    etag = _setting(db, RULE_ETAG_KEY, "")
    stored_hash = _setting(db, RULE_SHA256_KEY, "")
    if etag and stored_hash:
        headers["If-None-Match"] = etag

    try:
        with requests.get(RULE_MANIFEST_URL, timeout=RULE_TIMEOUT_SECONDS, stream=True) as manifest_response:
            manifest_response.raise_for_status()
            expected_hash = _expected_ruleset_hash(
                _response_json(manifest_response, max_bytes=RULE_MANIFEST_MAX_BYTES)
            )
        with requests.get(RULE_SOURCE_URL, timeout=RULE_TIMEOUT_SECONDS, headers=headers, stream=True) as response:
            if response.status_code == 304:
                if stored_hash != expected_hash:
                    raise ValueError("Remote insight rules manifest changed without ruleset content")
                _save_setting(db, RULE_FETCHED_AT_KEY, utc_now().replace(tzinfo=None).isoformat())
                _update_diagnostic(db, "healthy", f"Insights engine rules unchanged: {_ruleset_state_message(db)}")
                db.commit()
                return {"status": "unchanged", "source": "database", "version": _setting(db, RULE_VERSION_KEY, str(bundled["version"])), "count": len(active_rules(db))}
            response.raise_for_status()
            ruleset_content = _read_capped_content(response, max_bytes=RULESET_MAX_BYTES)
        actual_hash = hashlib.sha256(ruleset_content).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError("Remote insight rules SHA-256 verification failed")
        remote_data = json.loads(ruleset_content)
        if not isinstance(remote_data, dict):
            raise ValueError("Remote insight rules response must be an object")
        remote = import_ruleset(db, remote_data, source="remote")
        _delete_setting(db, "insight_rules.cache_json")
        _save_setting(db, RULE_FETCHED_AT_KEY, utc_now().replace(tzinfo=None).isoformat())
        _save_setting(db, RULE_VERSION_KEY, str(remote.get("version") or ""))
        _save_setting(db, RULE_SOURCE_KEY, RULE_SOURCE_URL)
        _save_setting(db, RULE_SHA256_KEY, actual_hash)
        if response.headers.get("ETag"):
            _save_setting(db, RULE_ETAG_KEY, str(response.headers["ETag"]))
        active_count = len(active_rules(db))
        _update_diagnostic(db, "healthy", f"Insights engine rules loaded: {_ruleset_state_message(db)}")
        db.commit()
        return {"status": "updated", "source": "remote", "version": remote.get("version"), "count": active_count}
    except Exception as exc:
        active_count = len(active_rules(db))
        last_known_version = _setting(db, RULE_VERSION_KEY, "")
        last_fetched_at = _setting(db, RULE_FETCHED_AT_KEY, "")
        # RULE_FETCHED_AT_KEY/RULE_VERSION_KEY are only ever written on a
        # successful fetch (including a 304) - never here - so their presence
        # is exactly "has a remote fetch ever succeeded", and their value is
        # exactly the last known-good remote state. That makes the fallback
        # message precise about what's actually active instead of the vague
        # "database/bundled rules" it used to say, which could mean either
        # bundled-only or bundled-plus-previously-fetched-remote.
        if last_known_version and last_fetched_at:
            message = (
                f"Remote insight rules fetch failed ({exc}). "
                f"Using last known-good remote rules: v{last_known_version} (fetched {last_fetched_at}), plus pre-shipped rules."
            )
        else:
            message = f"Remote insight rules unreachable ({exc}). Using pre-shipped rules only (v{bundled['version']})."
        _update_diagnostic(db, "warning", message)
        db.commit()
        return {"status": "failed", "source": "database", "version": last_known_version or str(bundled["version"]), "count": active_count, "error": str(exc)}


def _insight_in_cooldown(
    db: Session, insight_type: str, *, correlation_key: str, window_start: datetime, window_end: datetime,
) -> bool:
    return db.query(Insight).filter(
        Insight.type == insight_type,
        Insight.correlation_key == correlation_key,
        Insight.occurred_at >= window_start,
        Insight.occurred_at <= window_end,
    ).first() is not None


def _insight_exists(db: Session, insight_type: str, event_ids: list[int]) -> bool:
    return db.query(Insight).filter(Insight.type == insight_type, Insight.related_event_ids == event_ids).first() is not None


def apply_declarative_insight_rules(db: Session, event: Event) -> None:
    if not event.ip or not event.path:
        return
    path = event.path.lower()
    for rule in active_rules(db):
        if rule.kind != "web_probe":
            continue
        options = rule.match_options or {}
        if event.event_type not in rule.event_types:
            continue
        if not (any(pattern.lower() in path for pattern in rule.path_contains_any)
                or any(pattern.lower() == path for pattern in options.get("path_equals_any", []))
                or any(path.startswith(pattern.lower()) for pattern in options.get("path_prefix_any", []))):
            continue
        if options.get("status_codes") and event.status_code not in options["status_codes"]:
            continue
        if options.get("methods") and (not event.method or event.method.upper() not in options["methods"]):
            continue
        window_start = event.event_time - timedelta(minutes=rule.window_minutes)
        insight_ip = event.ip if rule.group_by == "ip" else None
        correlation_key = ip_correlation_key(event.ip) if rule.group_by == "ip" else "rule"
        if _insight_in_cooldown(db, rule.id, correlation_key=correlation_key,
                                window_start=window_start, window_end=event.event_time):
            continue
        path_matches = or_(
            *(func.lower(Event.path).contains(pattern.lower(), autoescape=True) for pattern in rule.path_contains_any),
            *(func.lower(Event.path) == pattern.lower() for pattern in options.get("path_equals_any", [])),
            *(func.lower(Event.path).startswith(pattern.lower(), autoescape=True) for pattern in options.get("path_prefix_any", [])),
        )
        matching_query = db.query(Event.id, Event.ip, Event.asset_id).filter(
            Event.event_type.in_(rule.event_types),
            Event.event_time >= window_start,
            Event.event_time <= event.event_time,
            Event.path.isnot(None),
            path_matches,
        )
        if options.get("status_codes"):
            matching_query = matching_query.filter(Event.status_code.in_(options["status_codes"]))
        if options.get("methods"):
            matching_query = matching_query.filter(func.upper(Event.method).in_(options["methods"]))
        if rule.group_by == "ip":
            matching_query = matching_query.filter(Event.ip == event.ip)
        event_count, distinct_ip_count, distinct_asset_count = matching_query.with_entities(
            func.count(Event.id), func.count(func.distinct(func.nullif(Event.ip, ""))),
            func.count(func.distinct(Event.asset_id)),
        ).one()
        if event_count < rule.threshold or distinct_ip_count < rule.min_distinct_ips:
            continue
        sample_order = (Event.event_time.desc(), Event.id.desc()) if _schema_major(rule.schema_version) == 2 else (Event.id.desc(),)
        matching_events = matching_query.order_by(*sample_order).limit(MAX_SAMPLE_IDS).all()
        related_ids = sorted(candidate_id for candidate_id, _ip, _asset_id in matching_events)
        if _insight_exists(db, rule.id, related_ids):
            continue
        asset_id = matching_query.with_entities(func.min(Event.asset_id)).scalar() if distinct_asset_count == 1 else None
        # Preserve schema-1's global pattern grouping, including mixed asset/NULL inputs.
        has_unassigned = matching_query.filter(Event.asset_id.is_(None)).first() is not None
        record_insight(db,
            type=rule.id,
            confidence=rule.confidence,
            level=rule.level,
            title=rule.title,
            description=f"{rule.description} Matched {event_count} event(s) from {distinct_ip_count} IP(s) within {rule.window_minutes} minutes.",
            related_event_ids=related_ids,
            ip=insight_ip,
            asset_id=event.asset_id if rule.group_by == "ip" else asset_id if not has_unassigned else None,
            occurred_at=event.event_time,
            correlation_key=correlation_key,
            dedupe_key=f"{rule.id}:window:{correlation_key}:{utc_iso(event.event_time)}",
            evidence={
                "version": 1, "kind": "heuristic", "rule_id": rule.id, "rule_source": rule.source,
                **({"ruleset_version": rule.ruleset_version} if rule.ruleset_version else {}),
                "window_start": utc_iso(window_start), "window_end": utc_iso(event.event_time),
                "event_count": event_count, "distinct_ip_count": distinct_ip_count,
                "distinct_asset_count": distinct_asset_count, "sample_count": len(related_ids),
                "sample_truncated": event_count > len(related_ids),
            },
        )


def debug_summary(db: Session) -> list[str]:
    rules = active_rules(db)
    return [
        f"source_url: {RULE_SOURCE_URL}",
        f"hash_manifest_url: {RULE_MANIFEST_URL}",
        f"stored_rules: {len(rules)}",
        f"remote_fetched_at: {_setting(db, RULE_FETCHED_AT_KEY, '')}",
        f"remote_ruleset_version: {_setting(db, RULE_VERSION_KEY, '')}",
        f"remote_ruleset_sha256: {_setting(db, RULE_SHA256_KEY, '')}",
        "rules:",
        *[f"- {rule.id}: {rule.title} (source={rule.source}; schema={rule.schema_version}; ruleset={rule.ruleset_version})" for rule in rules],
    ]
