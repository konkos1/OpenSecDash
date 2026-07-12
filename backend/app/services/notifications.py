from sqlalchemy.orm import Session

from app.models.core import NotificationRule


DEFAULT_NOTIFICATION_RULES = (
    {
        "rule_id": "core.crowdsec_ban",
        "name": "CrowdSec ban",
        "source": "event",
        "match_types": ["security.ban"],
        "min_severity": "warning",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 1,
    },
    {
        "rule_id": "core.scanner_detected",
        "name": "Scanner detected",
        "source": "insight",
        "match_types": ["*"],
        "min_severity": "high",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 5,
    },
    {
        "rule_id": "core.asset_offline",
        "name": "Asset offline",
        "source": "event",
        "match_types": ["system.asset_offline"],
        "min_severity": "warning",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 60,
    },
    {
        "rule_id": "core.plugin_error",
        "name": "Plugin error",
        "source": "event",
        "match_types": ["system.plugin_error"],
        "min_severity": "error",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 60,
    },
)


def seed_default_notification_rules(db: Session) -> None:
    """Add missing built-in notification rules without changing user settings."""
    existing_rule_ids = {rule_id for (rule_id,) in db.query(NotificationRule.rule_id).all()}
    for rule in DEFAULT_NOTIFICATION_RULES:
        if rule["rule_id"] not in existing_rule_ids:
            db.add(NotificationRule(**rule))
