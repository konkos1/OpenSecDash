from pathlib import Path

from app.models.core import Diagnostic, InsightRule
from app.plugins.base import Plugin, PluginMetadata
from app.plugins.manager import PluginManager
from app.services.insight_rules import _ruleset_state_message, active_rules


class InsightRulesPlugin(Plugin):
    metadata = PluginMetadata(id="test_insights", name="Test Insights")

    def insight_rules(self) -> dict:
        return {
            "schema_version": 1,
            "ruleset_version": "2026-07-12",
            "rules": [
                {
                    "id": "test.plugin_rule",
                    "title": "Plugin rule",
                    "event_types": ["access.error"],
                    "path_contains_any": ["/plugin-test"],
                }
            ],
        }


class InvalidInsightRulesPlugin(Plugin):
    metadata = PluginMetadata(id="invalid_insights", name="Invalid Insights")

    def insight_rules(self) -> dict:
        return {"schema_version": 1, "rules": "not-a-list"}


def test_plugin_insight_rules_are_imported_through_core_ruleset_validation(db_session):
    manager = PluginManager(Path("/not-used"))
    manager.plugins = {"test_insights": InsightRulesPlugin()}

    manager.seed_database(db_session)

    rule = db_session.query(InsightRule).filter_by(rule_id="test.plugin_rule").one()
    assert rule.source == "plugin:test_insights"
    assert [active_rule.id for active_rule in active_rules(db_session)] == ["test.plugin_rule"]
    assert "source=plugin:test_insights" in _ruleset_state_message(db_session)
    assert "plugin_sources=plugin:test_insights" in _ruleset_state_message(db_session)


def test_invalid_plugin_insight_rules_do_not_abort_seeding(db_session):
    manager = PluginManager(Path("/not-used"))
    manager.plugins = {"invalid_insights": InvalidInsightRulesPlugin()}

    manager.seed_database(db_session)

    diagnostic = db_session.query(Diagnostic).filter_by(plugin="invalid_insights", component="plugin").one()
    assert diagnostic.status == "warning"
    assert diagnostic.last_error == "Insight rules import failed: Insight ruleset must contain a rules list"


def test_seed_database_deactivates_rules_for_unloaded_plugins_and_invalidates_cache(db_session):
    db_session.add(
        InsightRule(
            rule_id="test.removed_rule",
            source="plugin:removed_plugin",
            title="Removed rule",
            event_types=["access.error"],
            path_contains_any=["/removed"],
        )
    )
    db_session.commit()
    assert [rule.id for rule in active_rules(db_session)] == ["test.removed_rule"]

    manager = PluginManager(Path("/not-used"))
    manager.seed_database(db_session)

    stale_rule = db_session.query(InsightRule).filter_by(rule_id="test.removed_rule").one()
    assert stale_rule.is_active is False
    assert "test.removed_rule" not in [rule.id for rule in active_rules(db_session)]


def test_plugin_collisions_are_atomic_and_do_not_block_following_plugin(db_session):
    from app.services.insight_rules import import_bundled_rules, import_ruleset

    import_bundled_rules(db_session)
    import_ruleset(db_session, InsightRulesPlugin().insight_rules(), source="plugin:owner")

    remote = InsightRulesPlugin().insight_rules()
    remote["rules"][0]["id"] = "test.remote"
    import_ruleset(db_session, remote, source="remote")

    for conflict in ("web.wordpress_scan", "security_ban_observed", "test.remote", "test.plugin_rule"):
        class CollidingPlugin(InsightRulesPlugin):
            metadata = PluginMetadata(id="collision", name="Collision")

            def insight_rules(self) -> dict:
                data = super().insight_rules()
                data["rules"][0]["id"] = "test.partial"
                data["rules"].append({**data["rules"][0], "id": conflict})
                return data

        class FollowingPlugin(InsightRulesPlugin):
            metadata = PluginMetadata(id="following", name="Following")

            def insight_rules(self) -> dict:
                data = super().insight_rules()
                data["rules"][0]["id"] = "unnamespaced"
                return data

        manager = PluginManager(Path("/not-used"))
        manager.plugins = {"owner": InsightRulesPlugin(), "collision": CollidingPlugin(),
                           "following": FollowingPlugin()}
        # Keep the owner source loaded without importing a second copy under another ID.
        manager.plugins["owner"].metadata = PluginMetadata(id="owner", name="Owner")
        manager.seed_database(db_session)
        assert db_session.query(InsightRule).filter_by(rule_id="test.partial").first() is None
        assert db_session.query(InsightRule).filter_by(rule_id="unnamespaced").one().source == "plugin:following"
        diagnostic = db_session.query(Diagnostic).filter_by(plugin="collision", component="plugin").one()
        assert diagnostic.status == "warning" and conflict in diagnostic.last_error


def test_database_import_failure_keeps_seeding_transaction_usable(monkeypatch, db_session):
    from app.models.settings import Setting
    from app.services import insight_rules

    class FollowingPlugin(InsightRulesPlugin):
        metadata = PluginMetadata(id="following", name="Following")

        def insight_rules(self) -> dict:
            data = super().insight_rules()
            data["rules"][0]["id"] = "test.following"
            return data

    original_sync = insight_rules.sync_insight_notification_rules

    def failing_sync(db):
        row = db.query(InsightRule).filter_by(rule_id="test.plugin_rule").first()
        if row is not None:
            # A real NOT NULL violation puts the Session into failed state until rollback.
            row.title = None
            db.flush()
        original_sync(db)

    monkeypatch.setattr(insight_rules, "sync_insight_notification_rules", failing_sync)
    db_session.add(Setting(key="test.outer", value="preserved"))
    manager = PluginManager(Path("/not-used"))
    manager.plugins = {"test_insights": InsightRulesPlugin(), "following": FollowingPlugin()}
    manager.seed_database(db_session)

    assert db_session.query(InsightRule).filter_by(rule_id="test.plugin_rule").first() is None
    assert db_session.query(InsightRule).filter_by(rule_id="test.following").one().source == "plugin:following"
    assert db_session.query(Setting).filter_by(key="test.outer").one().value == "preserved"
    diagnostic = db_session.query(Diagnostic).filter_by(plugin="test_insights", component="plugin").one()
    assert diagnostic.status == "warning" and "NOT NULL" in diagnostic.last_error
