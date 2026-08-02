from app.core import plugin_registry
from app.models.core import Insight, InsightRule, Notification, NotificationRule
from app.plugins.manager import get_plugin_manager
from app.services.insight_catalog import insight_availability, insight_definitions
from app.services.insight_rules import import_bundled_rules, import_ruleset
from app.services.notifications import (
    LEGACY_INSIGHT_NOTIFICATION_RULE_ID,
    handle_insight,
    insight_notification_rule_id,
    invalidate_rules_cache,
    notification_rule_views,
    seed_default_notification_rules,
    sync_insight_notification_rules,
)
from app.services.settings import save_setting


def _ruleset(rule_id: str, title: str, event_types: list[str]) -> dict:
    return {
        "schema_version": 1,
        "ruleset_version": "test",
        "rules": [
            {
                "id": rule_id,
                "title": title,
                "event_types": event_types,
                "path_contains_any": ["/test"],
            }
        ],
    }


def test_bundled_insights_get_independent_disabled_notification_rules(db_session):
    import_bundled_rules(db_session)

    rule = db_session.query(NotificationRule).filter_by(rule_id=insight_notification_rule_id("web.wordpress_scan")).one()
    assert rule.name == "Possible WordPress scan"
    assert rule.source == "insight"
    assert rule.match_types == ["web.wordpress_scan"]
    assert rule.enabled is False


def test_ruleset_title_update_preserves_notification_choice(db_session):
    import_ruleset(db_session, _ruleset("test.renamed", "Original title", ["access.error"]), source="remote")
    notification_rule = db_session.query(NotificationRule).filter_by(rule_id=insight_notification_rule_id("test.renamed")).one()
    notification_rule.enabled = True
    notification_rule.cooldown_minutes = 30
    db_session.flush()

    import_ruleset(db_session, _ruleset("test.renamed", "Updated title", ["access.error"]), source="remote")

    updated = db_session.query(NotificationRule).filter_by(rule_id=insight_notification_rule_id("test.renamed")).one()
    assert updated.name == "Updated title"
    assert updated.enabled is True
    assert updated.cooldown_minutes == 30


def test_legacy_wildcard_migrates_high_insights_without_remaining_active(db_session):
    db_session.add(
        NotificationRule(
            rule_id=LEGACY_INSIGHT_NOTIFICATION_RULE_ID,
            name="Scanner detected",
            source="insight",
            match_types=["*"],
            min_severity="high",
            enabled=True,
        )
    )
    db_session.flush()

    seed_default_notification_rules(db_session)

    assert db_session.query(NotificationRule).filter_by(rule_id=LEGACY_INSIGHT_NOTIFICATION_RULE_ID).one().enabled is False
    assert db_session.query(NotificationRule).filter_by(rule_id=insight_notification_rule_id("security_ban_observed")).one().enabled is True


def test_insight_requires_enabled_plugins_that_produce_all_input_event_types(db_session):
    assert plugin_registry.ids_producing_event_type("security.ban") == ["crowdsec"]
    assert plugin_registry.ids_producing_event_type("access.error") == ["traefik_log"]
    definitions = insight_definitions(db_session)
    definition = definitions["ban_after_access"]
    save_setting(db_session, "plugin.crowdsec.enabled", "true")
    save_setting(db_session, "plugin.traefik_log.enabled", "false")
    db_session.flush()

    unavailable = insight_availability(db_session, definition)
    assert unavailable.available is False
    assert unavailable.missing_event_type_groups == (("access.error",),)

    save_setting(db_session, "plugin.traefik_log.enabled", "true")
    assert insight_availability(db_session, definition).available is True


def test_plugin_owned_rule_requires_its_owner_even_with_an_event_producer(db_session):
    original_plugins = get_plugin_manager()
    plugin_registry.register_plugins(
        [
            plugin_registry.RegisteredPlugin(
                id="owner",
                name="Owner",
                capabilities=("insight",),
            ),
            plugin_registry.RegisteredPlugin(
                id="producer",
                name="Producer",
                capabilities=("datasource",),
                event_types=("access.error",),
            ),
        ]
    )
    try:
        save_setting(db_session, "plugin.owner.enabled", "false")
        save_setting(db_session, "plugin.producer.enabled", "true")
        import_ruleset(db_session, _ruleset("test.owned", "Owned", ["access.error"]), source="plugin:owner")

        definition = insight_definitions(db_session)["test.owned"]
        assert insight_availability(db_session, definition).available is False

        save_setting(db_session, "plugin.owner.enabled", "true")
        assert insight_availability(db_session, definition).available is True
    finally:
        original_plugins.discover()


def test_unavailable_rule_keeps_choice_but_does_not_queue(db_session, _test_secret_key):
    import_bundled_rules(db_session)
    save_setting(db_session, "notifications.enabled", "true")
    save_setting(db_session, "notifications.smtp_host", "smtp.example")
    save_setting(db_session, "notifications.smtp_sender", "sender@example")
    save_setting(db_session, "notifications.smtp_recipient", "admin@example")
    save_setting(db_session, "plugin.traefik_log.enabled", "false")
    rule_id = insight_notification_rule_id("web.wordpress_scan")
    notification_rule = db_session.query(NotificationRule).filter_by(rule_id=rule_id).one()
    notification_rule.enabled = True
    db_session.commit()
    invalidate_rules_cache()

    insight = Insight(type="web.wordpress_scan", level="medium", title="Possible WordPress scan")
    db_session.add(insight)
    handle_insight(db_session, insight)

    assert db_session.query(Notification).filter_by(rule_id=rule_id).count() == 0
    assert db_session.query(NotificationRule).filter_by(rule_id=rule_id).one().enabled is True
    view = next(item for item in notification_rule_views(db_session, "en") if item.rule.rule_id == rule_id)
    assert view.available is False
    assert "access.denied / access.error" in view.unavailable_reason


def test_removed_insight_definition_becomes_unavailable_without_losing_choice(db_session):
    db_session.add(
        InsightRule(
            rule_id="test.removed",
            source="remote",
            title="Removed",
            event_types=["access.error"],
            path_contains_any=["/removed"],
        )
    )
    db_session.flush()
    sync_insight_notification_rules(db_session)
    notification_rule = db_session.query(NotificationRule).filter_by(rule_id=insight_notification_rule_id("test.removed")).one()
    notification_rule.enabled = True
    db_session.query(InsightRule).filter_by(rule_id="test.removed").one().is_active = False
    db_session.flush()

    view = next(item for item in notification_rule_views(db_session, "en") if item.rule.rule_id == notification_rule.rule_id)
    assert view.available is False
    assert notification_rule.enabled is True
