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
