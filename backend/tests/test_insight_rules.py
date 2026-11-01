import hashlib
import json

import pytest

from app.models.core import Diagnostic, Insight
from app.models.settings import Setting
from app.services.events import store_event
from app.services.insight_rules import RULE_MANIFEST_URL, RULE_SOURCE_URL, parse_rules, refresh_insight_rules


class _StreamedResponse:
    """Mimics a streamed ``requests`` response backed by ``self.content``."""

    @property
    def content(self) -> bytes:
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        content = self.content
        for start in range(0, len(content), chunk_size):
            yield content[start : start + chunk_size]


class _Response(_StreamedResponse):
    status_code = 200
    headers = {"ETag": "test-etag"}

    def raise_for_status(self):
        return None

    @property
    def content(self):
        return json.dumps(
            {
                "schema_version": 1.1,
                "ruleset_version": "2026-07-02",
                "rules": [
                    {
                        "id": "web.test_probe",
                        "title": "Test probe",
                        "description": "Test probe description.",
                        "level": "medium",
                        "confidence": 0.7,
                        "event_types": ["access.error"],
                        "path_contains_any": ["/test-probe"],
                        "group_by": "ip",
                        "window_minutes": 5,
                        "threshold": 1,
                    }
                ],
            },
            separators=(",", ":"),
        ).encode()


class _ManifestResponse(_StreamedResponse):
    status_code = 200
    headers = {}

    def raise_for_status(self):
        return None

    @property
    def content(self):
        return json.dumps(
            {
                "schema_version": 1,
                "path": "/rules/insights-rules.json",
                "sha256": hashlib.sha256(_Response().content).hexdigest(),
                "expires": "2099-12-31",
            }
        ).encode()


def _remote_response(url):
    if url == RULE_MANIFEST_URL:
        return _ManifestResponse()
    assert url == RULE_SOURCE_URL
    return _Response()


def test_default_declarative_rules_create_web_insight(db_session):
    for _ in range(3):
        store_event(
            db_session,
            source="test",
            plugin="traefik_log",
            event_type="access.error",
            severity="warning",
            ip="8.8.8.8",
            path="/wp-login.php",
            status_code=404,
        )
    db_session.commit()

    insight = db_session.query(Insight).filter_by(type="web.wordpress_scan").one()

    assert insight.title == "Possible WordPress scan"
    assert insight.ip == "8.8.8.8"


def test_refresh_insight_rules_uses_hardcoded_source_url(monkeypatch, db_session):
    calls = []

    def fake_get(url, timeout, headers=None, stream=False):
        calls.append((url, timeout, headers))
        return _remote_response(url)

    monkeypatch.setattr("app.services.insight_rules.requests.get", fake_get)

    result = refresh_insight_rules(db_session, force=True)

    assert result == {"status": "updated", "source": "remote", "version": "2026-07-02", "count": 15}
    diagnostic = _ruleset_diagnostic(db_session)
    assert "source=bundled+remote" in diagnostic.last_error
    assert "bundled_version=2026-10-24" in diagnostic.last_error
    assert "remote_version=2026-07-02" in diagnostic.last_error
    assert "bundled=14" in diagnostic.last_error
    assert "remote=1" in diagnostic.last_error
    assert calls[0][0] == RULE_MANIFEST_URL
    assert calls[1][0] == RULE_SOURCE_URL
    assert db_session.query(Setting).filter_by(key="insight_rules.version").one().value == "2026-07-02"
    stored_hash = db_session.query(Setting).filter_by(key="insight_rules.sha256").one().value
    assert stored_hash == hashlib.sha256(_Response().content).hexdigest()


def _ruleset_diagnostic(db_session):
    return db_session.query(Diagnostic).filter_by(plugin="insight_rules", component="ruleset").one()


def test_refresh_insight_rules_reports_bundled_only_fallback_on_first_ever_failure(monkeypatch, db_session):
    # No RULE_FETCHED_AT_KEY/RULE_VERSION_KEY yet - a remote fetch has never
    # succeeded, so the fallback is unambiguously "bundled rules only".
    def fake_get(url, timeout, headers=None, stream=False):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr("app.services.insight_rules.requests.get", fake_get)

    result = refresh_insight_rules(db_session, force=True)

    assert result["status"] == "failed"
    diagnostic = _ruleset_diagnostic(db_session)
    assert diagnostic.status == "warning"
    assert "pre-shipped rules only" in diagnostic.last_error
    assert "last known-good remote" not in diagnostic.last_error


def test_refresh_insight_rules_reports_last_known_good_remote_on_later_failure(monkeypatch, db_session):
    # A previous fetch succeeded (version + timestamp already stored); a
    # later failure must say so precisely instead of the ambiguous old
    # "using database/bundled rules" message.
    monkeypatch.setattr(
        "app.services.insight_rules.requests.get",
        lambda url, timeout, headers=None, stream=False: _remote_response(url),
    )
    first = refresh_insight_rules(db_session, force=True)
    assert first["status"] == "updated"

    def fake_get_fails(url, timeout, headers=None, stream=False):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr("app.services.insight_rules.requests.get", fake_get_fails)
    result = refresh_insight_rules(db_session, force=True)

    assert result["status"] == "failed"
    assert result["version"] == "2026-07-02"
    diagnostic = _ruleset_diagnostic(db_session)
    assert diagnostic.status == "warning"
    assert "last known-good remote rules: v2026-07-02" in diagnostic.last_error
    assert "plus pre-shipped rules" in diagnostic.last_error


def test_refresh_insight_rules_reports_source_versions_from_cache(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.services.insight_rules.requests.get",
        lambda url, timeout, headers=None, stream=False: _remote_response(url),
    )
    first = refresh_insight_rules(db_session, force=True)
    assert first["status"] == "updated"

    skipped = refresh_insight_rules(db_session)

    assert skipped["status"] == "skipped"
    diagnostic = _ruleset_diagnostic(db_session)
    assert "loaded from database" in diagnostic.last_error
    assert "source=bundled+remote" in diagnostic.last_error
    assert "bundled_version=2026-10-24" in diagnostic.last_error
    assert "remote_version=2026-07-02" in diagnostic.last_error
    assert "bundled=14" in diagnostic.last_error
    assert "remote=1" in diagnostic.last_error


def test_refresh_insight_rules_rejects_ruleset_with_wrong_hash(monkeypatch, db_session):
    class _WrongHashManifest(_ManifestResponse):
        @property
        def content(self):
            return json.dumps(
                {
                    "schema_version": 1,
                    "path": "/rules/insights-rules.json",
                    "sha256": "0" * 64,
                    "expires": "2099-12-31",
                }
            ).encode()

    def fake_get(url, timeout, headers=None, stream=False):
        return _WrongHashManifest() if url == RULE_MANIFEST_URL else _Response()

    monkeypatch.setattr("app.services.insight_rules.requests.get", fake_get)

    result = refresh_insight_rules(db_session, force=True)

    assert result["status"] == "failed"
    assert "SHA-256 verification failed" in result["error"]
    assert db_session.query(Setting).filter_by(key="insight_rules.version").first() is None


def test_refresh_insight_rules_rejects_oversized_ruleset_without_buffering(monkeypatch, db_session):
    from app.services import insight_rules

    class _OversizedRuleset(_Response):
        # Simulate a hostile source that keeps streaming past the cap. The
        # body is never fully materialised: iteration must stop once the limit
        # is exceeded, so we assert only a bounded prefix is ever requested.
        chunks_read = 0

        def iter_content(self, chunk_size):
            while True:
                type(self).chunks_read += 1
                yield b"x" * chunk_size

    def fake_get(url, timeout, headers=None, stream=False):
        assert stream is True
        return _ManifestResponse() if url == RULE_MANIFEST_URL else _OversizedRuleset()

    monkeypatch.setattr("app.services.insight_rules.requests.get", fake_get)

    result = refresh_insight_rules(db_session, force=True)

    assert result["status"] == "failed"
    assert "too large" in result["error"]
    # Reading stops as soon as the cap is passed - a couple of chunks, not the
    # unbounded stream a full buffer would have consumed.
    assert _OversizedRuleset.chunks_read <= (insight_rules.RULESET_MAX_BYTES // insight_rules._READ_CHUNK_BYTES) + 2
    assert db_session.query(Setting).filter_by(key="insight_rules.version").first() is None


def test_refresh_insight_rules_rejects_oversized_content_length(monkeypatch, db_session):
    class _DeclaredOversized(_Response):
        headers = {"ETag": "test-etag", "Content-Length": str(10 * 1024 * 1024)}

        def iter_content(self, chunk_size):
            raise AssertionError("body must not be read once Content-Length exceeds the cap")

    def fake_get(url, timeout, headers=None, stream=False):
        return _ManifestResponse() if url == RULE_MANIFEST_URL else _DeclaredOversized()

    monkeypatch.setattr("app.services.insight_rules.requests.get", fake_get)

    result = refresh_insight_rules(db_session, force=True)

    assert result["status"] == "failed"
    assert "too large" in result["error"]


def test_schema_version_allows_same_major_minor_updates():
    rules = parse_rules(
        {
            "schema_version": 1.1,
            "ruleset_version": "2026-07-02",
            "rules": [
                {
                    "id": "web.test_probe",
                    "title": "Test probe",
                    "event_types": ["access.error"],
                    "path_contains_any": ["/test"],
                }
            ],
        }
    )

    assert rules[0].id == "web.test_probe"


def test_schema_version_rejects_breaking_major_updates():
    with pytest.raises(ValueError, match="Unsupported insight rules schema_version"):
        parse_rules(
            {
                "schema_version": 3,
                "ruleset_version": "2026-07-02",
                "rules": [
                    {
                        "id": "web.test_probe",
                        "title": "Test probe",
                        "event_types": ["access.error"],
                        "path_contains_any": ["/test"],
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    ("rule_id", "title", "message"),
    [
        ("x" * 101, "Test probe", "invalid id"),
        ("web.test_probe", "x" * 256, "invalid title"),
    ],
)
def test_ruleset_rejects_metadata_that_does_not_fit_storage(rule_id, title, message):
    with pytest.raises(ValueError, match=message):
        parse_rules(
            {
                "schema_version": 1,
                "ruleset_version": "test",
                "rules": [
                    {
                        "id": rule_id,
                        "title": title,
                        "event_types": ["access.error"],
                        "path_contains_any": ["/test"],
                    }
                ],
            }
        )


def _ruleset(*rules):
    return {"schema_version": 1, "ruleset_version": "test", "rules": list(rules)}


def _rule(rule_id="web.wordpress_scan", **changes):
    return {"id": rule_id, "title": "Remote override", "event_types": ["access.error"],
            "path_contains_any": ["/override"], **changes}


@pytest.mark.parametrize("field,value", [
    ("id", " "), ("id", 12), ("title", " "), ("title", False),
    ("description", 12), ("description", "x" * 2001),
    ("level", "unknown"), ("level", []), ("confidence", 2.0),
    ("confidence", -0.1), ("confidence", float("nan")),
    ("confidence", float("inf")), ("confidence", True), ("confidence", "0.7"),
    ("threshold", True), ("threshold", "2"), ("threshold", 1.5),
    ("window_minutes", False), ("window_minutes", "5"),
    ("path_contains_any", [""]), ("path_contains_any", ["  "]),
    ("event_types", [""]), ("event_types", [" "]), ("event_types", [False]),
    ("event_types", ["x" * 51]), ("event_types", ["access.error"] * 51),
    ("group_by", []),
])
def test_import_rejects_invalid_second_rule_without_partial_changes(db_session, field, value):
    from app.models.core import InsightRule
    from app.services.insight_rules import import_ruleset

    with pytest.raises(ValueError):
        import_ruleset(db_session, _ruleset(_rule("test.first"), _rule(**{field: value})), source="remote")
    assert db_session.query(InsightRule).count() == 0


@pytest.mark.parametrize("version", [True, None, [], {}, "1.bad"])
def test_ruleset_rejects_invalid_schema_types(version):
    with pytest.raises(ValueError):
        parse_rules({**_ruleset(), "schema_version": version})


@pytest.mark.parametrize("version", [12, None, "x" * 21])
def test_ruleset_rejects_invalid_version_metadata(version):
    with pytest.raises(ValueError):
        parse_rules({**_ruleset(), "ruleset_version": version})


def test_ruleset_rejects_duplicate_ids():
    with pytest.raises(ValueError):
        parse_rules(_ruleset(_rule(), _rule()))


@pytest.mark.parametrize("confidence", [0, 0.0, 1, 1.0])
def test_confidence_boundaries_and_extra_metadata_remain_supported(confidence):
    rule = parse_rules(_ruleset(_rule(confidence=confidence, documentation={"note": "metadata"})))[0]
    assert rule.confidence == confidence


@pytest.mark.parametrize("mode", ["failure", "cache", "startup", "304"])
def test_remote_override_survives_bundled_loading(monkeypatch, db_session, mode):
    from app.core.time import utc_now
    from app.models.core import InsightRule
    from app.services.insight_rules import import_bundled_rules, import_ruleset

    import_bundled_rules(db_session)
    import_ruleset(db_session, _ruleset(_rule()), source="remote")
    for key, value in {"fetched_at": utc_now().replace(tzinfo=None).isoformat(),
                       "version": "test", "sha256": hashlib.sha256(_Response().content).hexdigest(),
                       "etag": "test-etag"}.items():
        db_session.add(Setting(key=f"insight_rules.{key}", value=value))
    db_session.commit()

    class NotModified(_Response):
        status_code = 304

    def fake_get(url, **kwargs):
        if mode == "304":
            return _ManifestResponse() if url == RULE_MANIFEST_URL else NotModified()
        raise ConnectionError("offline")

    monkeypatch.setattr("app.services.insight_rules.requests.get", fake_get)
    if mode == "startup":
        import_bundled_rules(db_session)
    else:
        result = refresh_insight_rules(db_session, force=mode != "cache")
        assert result["status"] == {"failure": "failed", "cache": "skipped", "304": "unchanged"}[mode]
    row = db_session.query(InsightRule).filter_by(rule_id="web.wordpress_scan").one()
    assert (row.source, row.title, row.path_contains_any) == ("remote", "Remote override", ["/override"])


@pytest.mark.parametrize("replacement", [[], [_rule("test.new")]])
def test_remote_replacement_restores_bundle_and_preserves_other_sources(db_session, replacement):
    from app.models.core import InsightRule, NotificationRule
    from app.services.insight_rules import import_bundled_rules, import_ruleset
    from app.services.notifications import insight_notification_rule_id

    import_bundled_rules(db_session)
    import_ruleset(db_session, _ruleset(_rule(), _rule("test.remote_only")), source="remote")
    import_ruleset(db_session, _ruleset(_rule("test.plugin")), source="plugin:test")
    notification = db_session.query(NotificationRule).filter_by(
        rule_id=insight_notification_rule_id("web.wordpress_scan")).one()
    notification.enabled = True
    notification.cooldown_minutes = 37
    notification.window_minutes = 19
    import_ruleset(db_session, _ruleset(*replacement), source="remote")
    row = db_session.query(InsightRule).filter_by(rule_id="web.wordpress_scan").one()
    assert row.source == "bundled" and row.title == "Possible WordPress scan" and row.is_active
    assert not db_session.query(InsightRule).filter_by(rule_id="test.remote_only").one().is_active
    assert db_session.query(InsightRule).filter_by(rule_id="test.plugin").one().is_active
    assert db_session.query(InsightRule).filter_by(rule_id="web.git_probe").one().is_active
    assert (notification.enabled, notification.cooldown_minutes, notification.window_minutes) == (True, 37, 19)


def test_failed_application_rolls_back_rules_and_catalog_but_keeps_outer_transaction(monkeypatch, db_session):
    from app.models.core import InsightRule, NotificationRule
    from app.services import insight_rules

    insight_rules.import_bundled_rules(db_session)
    cached = insight_rules.active_rules(db_session)
    original_count = db_session.query(NotificationRule).count()
    db_session.add(Setting(key="test.outer", value="preserved"))
    original_sync = insight_rules.sync_insight_notification_rules

    def failing_sync(db):
        original_sync(db)
        db.flush()
        raise ValueError("injected application failure")

    monkeypatch.setattr(insight_rules, "sync_insight_notification_rules", failing_sync)
    with pytest.raises(ValueError, match="injected"):
        insight_rules.import_ruleset(db_session, _ruleset(_rule(), _rule("test.partial")), source="remote")
    assert db_session.query(InsightRule).filter_by(rule_id="test.partial").first() is None
    assert db_session.query(InsightRule).filter_by(rule_id="web.wordpress_scan").one().source == "bundled"
    assert db_session.query(NotificationRule).count() == original_count
    assert insight_rules.active_rules(db_session) == cached
    db_session.commit()
    assert db_session.query(Setting).filter_by(key="test.outer").one().value == "preserved"


def test_invalid_remote_replacement_preserves_rules_and_refresh_metadata(monkeypatch, db_session):
    from app.models.core import InsightRule
    from app.services.insight_rules import import_bundled_rules, import_ruleset

    import_bundled_rules(db_session)
    import_ruleset(db_session, _ruleset(_rule()), source="remote")
    metadata = {"fetched_at": "2026-01-01T00:00:00", "version": "test", "etag": "old", "sha256": "a" * 64,
                "source": RULE_SOURCE_URL}
    for key, value in metadata.items():
        db_session.add(Setting(key=f"insight_rules.{key}", value=value))
    db_session.commit()
    payload = json.dumps(_ruleset(_rule(title="Changed"), _rule("test.invalid", confidence=2))).encode()

    class InvalidReplacement(_Response):
        @property
        def content(self):
            return payload

    class MatchingManifest(_ManifestResponse):
        @property
        def content(self):
            data = json.loads(super().content)
            data["sha256"] = hashlib.sha256(payload).hexdigest()
            return json.dumps(data).encode()

    monkeypatch.setattr("app.services.insight_rules.requests.get", lambda url, **kwargs:
                        MatchingManifest() if url == RULE_MANIFEST_URL else InvalidReplacement())
    assert refresh_insight_rules(db_session, force=True)["status"] == "failed"
    row = db_session.query(InsightRule).filter_by(rule_id="web.wordpress_scan").one()
    assert row.source == "remote" and row.title == "Remote override"
    assert db_session.query(InsightRule).filter_by(rule_id="test.invalid").first() is None
    for key, value in metadata.items():
        assert db_session.query(Setting).filter_by(key=f"insight_rules.{key}").one().value == value
