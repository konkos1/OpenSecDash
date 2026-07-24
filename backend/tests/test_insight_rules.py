import hashlib
import json

import pytest

from app.models.core import Diagnostic, Insight
from app.models.settings import Setting
from app.services.events import store_event
from app.services.insight_rules import RULE_MANIFEST_URL, RULE_SOURCE_URL, parse_rules, refresh_insight_rules


class _StreamedResponse:
    """Mimics a streamed ``requests`` response backed by ``self.content``."""

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

    assert result == {"status": "updated", "source": "remote", "version": "2026-07-02", "count": 7}
    diagnostic = _ruleset_diagnostic(db_session)
    assert "source=bundled+remote" in diagnostic.last_error
    assert "bundled_version=2026-07-11" in diagnostic.last_error
    assert "remote_version=2026-07-02" in diagnostic.last_error
    assert "bundled=6" in diagnostic.last_error
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
    assert "bundled_version=2026-07-11" in diagnostic.last_error
    assert "remote_version=2026-07-02" in diagnostic.last_error
    assert "bundled=6" in diagnostic.last_error
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
                "schema_version": 2,
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
