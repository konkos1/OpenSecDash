from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from app.api import pages
from app.models.core import Insight
from app.models.events import Event
from app.models.settings import Setting
from app.web import filters


@pytest.mark.parametrize(('kind', 'confidence', 'expected'), [
    ('direct', .8, 'certain'), ('direct', .7, 'possible'),
    ('heuristic', .9, 'probable'), ('correlation', .8, 'probable'),
    ('trend', .8, 'probable'), ('unknown', 1, 'possible'), (None, 1, 'possible'),
])
def test_certainty_depends_on_evidence_and_confidence(kind, confidence, expected):
    for level in ('low', 'medium', 'high', 'critical'):
        insight = Insight(level=level, confidence=confidence, evidence={'version': 1, 'kind': kind} if kind else None)
        assert filters.insight_certainty(insight)[1] == expected


@pytest.mark.parametrize('retained', [0, 1, 20])
def test_detail_retains_snapshot_and_bounds_examples(db_session, monkeypatch, retained):
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    events = [Event(plugin='traefik_log', event_type='access.denied') for _ in range(retained)]
    db_session.add_all(events)
    db_session.flush()
    insight = Insight(type='web.scan', title='Scan', related_event_ids=[e.id for e in events] + list(range(1000, 1020 - retained)),
        occurred_at=datetime(2026, 10, 31), evidence={'version': 1, 'kind': 'heuristic', 'event_count': 100, 'sample_count': 20, 'sample_truncated': True})
    db_session.add(insight)
    db_session.commit()
    monkeypatch.setattr(pages, 'render', lambda request, db, template, **context: context)
    context = cast(Any, pages.insight_page(cast(Any, SimpleNamespace()), insight.id, db_session))
    assert context['insight'].evidence['event_count'] == 100
    assert len(context['example_events']) == retained
    assert context['sample_count'] == 20


def test_detail_unknown_id_and_feature_gate(db_session):
    request = cast(Any, SimpleNamespace())
    with pytest.raises(HTTPException) as exc:
        pages.insight_page(request, 1, db_session)
    assert exc.value.status_code == 404
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        pages.insight_page(request, 1, db_session)
    assert exc.value.status_code == 404


def test_single_insight_drilldown_never_includes_unsampled_events(db_session, monkeypatch):
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    events = [Event(plugin='traefik_log', event_type='access.denied') for _ in range(21)]
    db_session.add_all(events)
    db_session.flush()
    insight = Insight(type='web.scan', title='Scan', related_event_ids=[e.id for e in events[:20]],
        evidence={'version': 1, 'kind': 'heuristic', 'event_count': 100, 'sample_count': 20})
    db_session.add(insight)
    db_session.commit()
    monkeypatch.setattr(pages, 'render', lambda request, db, template, **context: context)
    request = cast(Any, SimpleNamespace(query_params={}, url=SimpleNamespace(path='/events'), headers={}))
    result = cast(Any, pages.events_page(request, insight_id=insight.id, range='all', db=db_session))
    assert {e.id for e in result['events']} == {e.id for e in events[:20]}
    assert result['filters']['insight_id'] == insight.id


@pytest.mark.parametrize(('timezone', 'boundary'), [
    ('UTC', datetime(2026, 10, 25)),
    ('Europe/Berlin', datetime(2026, 10, 24, 22)),
    ('Europe/Berlin', datetime(2026, 3, 28, 23)),
])
def test_display_time_filter_and_order_agree_across_day_boundaries(db_session, timezone, boundary):
    from datetime import timedelta
    from app.core.time import format_datetime_for_timezone

    old = Insight(type='old', title='Old', occurred_at=boundary - timedelta(seconds=1), timestamp=boundary + timedelta(hours=12))
    new = Insight(type='new', title='New', occurred_at=boundary, timestamp=boundary + timedelta(hours=12))
    legacy = Insight(type='legacy', title='Legacy', timestamp=boundary + timedelta(seconds=1))
    db_session.add_all([old, new, legacy])
    db_session.commit()
    result = db_session.query(Insight).filter(pages.insight_display_time() >= boundary).order_by(pages.insight_display_time().desc()).all()
    assert [i.type for i in result] == ['legacy', 'new']
    assert format_datetime_for_timezone(new.occurred_at, timezone).split(' ')[0] != format_datetime_for_timezone(old.occurred_at, timezone).split(' ')[0]


def test_detail_renders_twenty_of_one_hundred_and_level2_scope(db_session):
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    examples = [Event(plugin='traefik_log', event_type='access.denied') for _ in range(20)]
    db_session.add_all(examples)
    db_session.flush()
    insight = Insight(type='web.chain', title='Chain', level='high', confidence=.8, ip='192.0.2.1', asset_id=None,
        related_event_ids=[e.id for e in examples], evidence={'version': 1, 'kind': 'correlation',
            'event_count': 100, 'sample_count': 20, 'sample_truncated': True, 'distinct_asset_count': 3})
    db_session.add(insight)
    db_session.commit()
    request = cast(Any, SimpleNamespace(query_params={}, url=SimpleNamespace(path='/insights/1'), headers={}, state=SimpleNamespace(), cookies={}))
    response = pages.insight_page(request, insight.id, db_session)
    html = bytes(response.body).decode()
    assert '100' in html and '20 / 20' in html
    assert 'Example events' in html and 'All 100' not in html
    assert 'banned participant IP' in html and 'multiple assets' in html
    assert 'Probable' in html and 'Certain' not in html.replace('Certainty', '')


def test_display_time_migration_and_query_plans(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.orm import Session
    from app.core import settings as settings_module

    database_url = f"sqlite:///{tmp_path / 'display-time.db'}"
    monkeypatch.setattr(settings_module.settings, 'database_url', database_url)
    config = Config('alembic.ini')
    command.upgrade(config, 'b9c0d1e2f3a4')
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO insights (timestamp,type,confidence,level,title,description) VALUES ('2026-10-31 12:00:00','legacy',0,'high','Legacy','')")
    command.upgrade(config, 'head')
    engine.dispose()
    with Session(engine) as db:
        query = pages.insight_counts_query(db, datetime(2026, 10, 31))
        compiled = str(query.statement.compile(dialect=sqlite.dialect(), compile_kwargs={'literal_binds': True}))
        plan = db.connection().exec_driver_sql('EXPLAIN QUERY PLAN ' + compiled).all()
        assert any('SEARCH insights USING INDEX ix_insights_display_time' in str(row) for row in plan)
        assert query.one()[1] == 1
        ordered = db.query(Insight).filter(pages.insight_display_time() >= datetime(2026, 10, 31)).order_by(pages.insight_display_time().desc()).limit(20)
        compiled = str(ordered.statement.compile(dialect=sqlite.dialect(), compile_kwargs={'literal_binds': True}))
        plan = db.connection().exec_driver_sql('EXPLAIN QUERY PLAN ' + compiled).all()
        assert any('ix_insights_display_time' in str(row) for row in plan)
    command.downgrade(config, 'b9c0d1e2f3a4')
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE name='ix_insights_display_time'").scalar() == 0
        assert connection.exec_driver_sql('SELECT title FROM insights').scalar() == 'Legacy'
    command.upgrade(config, 'head')
    engine.dispose()


@pytest.mark.parametrize('retained', [0, 1, 2])
def test_historical_ban_detail_keeps_fact_when_examples_disappear(db_session, retained):
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    examples = [Event(plugin='traefik_log', event_type='security.ban') for _ in range(retained)]
    db_session.add_all(examples)
    db_session.flush()
    insight = Insight(type='security_ban_observed', title='Security ban observed',
        description='A historical ban was observed.', level='high', confidence=.95,
        related_event_ids=[e.id for e in examples] + list(range(9998, 10000 - retained)),
        evidence={'version': 1, 'kind': 'direct', 'event_count': 2, 'sample_count': 2})
    db_session.add(insight)
    db_session.commit()
    request = cast(Any, SimpleNamespace(query_params={}, url=SimpleNamespace(path='/insights/1'), headers={}, state=SimpleNamespace(), cookies={}))
    html = bytes(pages.insight_page(request, insight.id, db_session).body).decode()
    assert 'Security ban observed' in html and '✓ Certain' in html
    assert 'do not establish a currently active ban' in html
    assert f'{retained} / 2' in html
    assert ('Example events no longer available' in html) == (retained < 2)


@pytest.mark.parametrize(('first', 'second', 'first_local', 'second_local'), [
    (datetime(2026, 3, 29, 0, 30), datetime(2026, 3, 29, 1, 30), '01:30:00', '03:30:00'),
    (datetime(2026, 10, 25, 0, 30), datetime(2026, 10, 25, 1, 30), '02:30:00', '02:30:00'),
])
def test_insight_times_keep_utc_identity_through_berlin_dst(db_session, first, second, first_local, second_local):
    db_session.add_all([
        Insight(type='first', title='First', occurred_at=first, timestamp=second),
        Insight(type='second', title='Second', occurred_at=second, timestamp=first),
    ])
    db_session.commit()
    ordered = db_session.query(Insight).order_by(pages.insight_display_time().desc()).all()
    assert [i.type for i in ordered] == ['second', 'first']
    first_html = str(filters.format_datetime({'timezone': 'Europe/Berlin'}, first))
    second_html = str(filters.format_datetime({'timezone': 'Europe/Berlin'}, second))
    assert first_local in first_html and second_local in second_html
    assert first.isoformat() + '+00:00' in first_html
    assert second.isoformat() + '+00:00' in second_html
