from datetime import UTC, datetime

import app.services.dashboard_metrics as dashboard_metrics
from app.models.core import AggregationDaily
from app.models.events import Event
from app.models.settings import Setting


def test_today_counts_uses_utc_rollup_path(db_session, monkeypatch):
    monkeypatch.setattr(dashboard_metrics, "utc_now", lambda: datetime(2026, 7, 11, 12, 0, tzinfo=UTC))
    db_session.add_all(
        [
            Setting(key="timezone", value="UTC"),
            Setting(key="plugin.crowdsec.enabled", value="true"),
            AggregationDaily(date="2026-07-11", metric="summary", key="bans", value=4),
        ]
    )
    db_session.commit()

    assert dashboard_metrics.today_counts(db_session)["bans"] == 4


def test_today_counts_uses_live_path_for_non_utc_day(db_session, monkeypatch):
    monkeypatch.setattr(dashboard_metrics, "utc_now", lambda: datetime(2026, 7, 11, 12, 0, tzinfo=UTC))
    db_session.add_all(
        [
            Setting(key="timezone", value="Europe/Berlin"),
            Setting(key="plugin.crowdsec.enabled", value="true"),
            Event(event_time=datetime(2026, 7, 11, 0, 0), event_type="security.ban", plugin="crowdsec"),
        ]
    )
    db_session.commit()

    assert dashboard_metrics.today_counts(db_session)["bans"] == 1
