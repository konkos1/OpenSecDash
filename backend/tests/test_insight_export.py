import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.base import Base

from app.database.dependencies import get_db
from app.models.core import Insight
from app.models.settings import Setting
from app.services.insight_export import serialize_insight
from app.api.insights import router


def finding(**values):
    return Insight(type='security_ban_observed', title='Security ban observed', level='high', confidence=.95,
        timestamp=datetime(2026, 11, 1), occurred_at=datetime(2026, 11, 1),
        evidence={'version': 1, 'kind': 'direct', 'event_count': 2, 'sample_count': 20},
        related_event_ids=list(range(1, 22)), **values)


def test_serializer_allowlist_bounds_and_retained_examples():
    insight = finding(description='password=synthetic-secret', ip='192.0.2.1')
    insight.id = 7
    insight.title = 'token=synthetic-secret on private.example.test /private'
    insight.evidence = {**(insight.evidence or {}), 'raw': 'synthetic-secret', 'event_count': 10**10000}
    mqtt = serialize_insight(insight)
    download = serialize_insight(insight, download=True)
    assert mqtt['schema_version'] == 1 and mqtt['certainty'] == 'certain'
    assert download['example_event_ids'] == list(range(1, 21))
    assert download['detail_path'] == '/insights/7'
    assert 'example_event_ids' not in mqtt and 'detail_path' not in mqtt
    for payload in (mqtt, download):
        encoded = json.dumps(payload, allow_nan=False).encode()
        assert len(encoded) <= 8192
        assert all(value not in encoded.decode() for value in ('synthetic-secret', 'private.example.test', '/private', '192.0.2.1'))
        assert 'raw' not in payload['evidence']


@pytest.mark.parametrize('type_value', ['bad/+/secret#', 'password=synthetic-secret', 'foreign_private_hostname'])
def test_foreign_metadata_cannot_escape_serializer(type_value):
    insight = finding()
    insight.id = 1
    insight.type = type_value
    insight.title = 'arbitrary secret metadata'
    payload = serialize_insight(insight)
    assert payload['type'].startswith('other.')
    assert all(value not in json.dumps(payload) for value in (type_value, 'arbitrary secret metadata'))
    assert all(value not in payload['type'] for value in ('+', '#', '/'))


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'export.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        yield db
    engine.dispose()


@pytest.fixture()
def export_client(db_session):
    app = FastAPI()
    app.include_router(router)
    from app.api.pages import router as pages_router
    app.include_router(pages_router)
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as client:
        yield client


def test_keyset_pagination_ties_legacy_and_frozen_window(db_session, export_client):
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    rows = [finding() for _ in range(5)]
    rows[2].occurred_at = None
    db_session.add_all(rows)
    db_session.commit()
    ids = []
    params = {'start': '2026-10-31T00:00:00Z', 'end': '2026-11-02T00:00:00Z', 'limit': 2}
    while True:
        response = export_client.get('/api/insights/export', params=params)
        assert response.status_code == 200, response.text
        data = response.json()
        ids.extend(item['id'] for item in data['items'])
        if not data['next_cursor']:
            break
        params = {'cursor': data['next_cursor'], 'limit': 2}
    assert ids == [row.id for row in reversed(rows)]
    assert len(set(ids)) == 5
    assert data['period']['start'] == '2026-10-31T00:00:00+00:00'


@pytest.mark.parametrize('params', [
    {'limit': 0}, {'limit': 501}, {'cursor': 'nonsense'}, {'cursor': 'x' * 2000},
    {'start': '2026-10-01T00:00:00Z', 'end': '2026-11-02T00:00:00Z'},
    {'start': '2026-11-02T00:00:00Z', 'end': '2026-11-01T00:00:00Z'},
    {'start': '2026-11-01'}, {'type': 'bad/#'}, {'type': 'missing'},
])
def test_invalid_export_parameters_rejected(db_session, export_client, params):
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    db_session.commit()
    assert export_client.get('/api/insights/export', params=params).status_code == 422


def test_feature_gate_and_default_day(db_session, export_client):
    assert export_client.get('/api/insights/export').status_code == 404
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    db_session.commit()
    response = export_client.get('/api/insights/export')
    assert response.status_code == 200
    period = response.json()['period']
    assert datetime.fromisoformat(period['end']) - datetime.fromisoformat(period['start']) == timedelta(days=1)
    assert datetime.fromisoformat(period['end']).tzinfo == UTC


def test_type_filtered_export_bounds_and_uses_time_index(db_session, export_client):
    from sqlalchemy import event
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    rows = [finding() for _ in range(4)]
    rows[0].occurred_at = datetime(2026, 10, 1)
    rows[1].type = 'manual_security_ban'
    db_session.add_all(rows)
    db_session.commit()
    statements = []
    def capture(conn, cursor, statement, parameters, *args):
        if 'FROM (SELECT insights.' in statement:
            statements.append((statement, parameters))
    event.listen(db_session.bind, 'before_cursor_execute', capture)
    params = {'start': '2026-10-31T00:00:00Z', 'end': '2026-11-02T00:00:00Z', 'type': 'security_ban_observed', 'limit': 1}
    response = export_client.get('/api/insights/export', params=params)
    data = response.json()
    assert [item['id'] for item in data['items']] == [rows[3].id]
    statement, parameters = statements[0]
    plan = db_session.connection().exec_driver_sql('EXPLAIN QUERY PLAN ' + statement, parameters).all()
    assert any('SEARCH insights USING INDEX ix_insights_display_time' in str(row) for row in plan)
    assert not any('USING INDEX ix_insights_type ' in str(row) for row in plan)
    response = export_client.get('/api/insights/export', params={'cursor': data['next_cursor']})
    assert [item['id'] for item in response.json()['items']] == [rows[2].id]
    assert response.json()['next_cursor'] is None
    assert response.headers['cache-control'] == 'no-store'
    assert 'attachment' in response.headers['content-disposition']


@pytest.mark.parametrize('data', [[], {}, {'start': 123, 'end': None, 'id': True, 'time': 'bad', 'type': []}])
def test_structurally_invalid_cursor_rejected(db_session, export_client, data):
    import base64
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    db_session.commit()
    token = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    assert export_client.get('/api/insights/export', params={'cursor': token}).status_code == 422


def test_export_keeps_summary_and_example_ids_after_retention(db_session, export_client):
    from app.models.events import Event
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    example = Event(plugin='traefik_log', event_type='security.ban')
    db_session.add(example)
    db_session.flush()
    row = finding()
    row.related_event_ids = [example.id]
    db_session.add(row)
    db_session.commit()
    db_session.delete(example)
    db_session.commit()
    response = export_client.get('/api/insights/export', params={'start': '2026-10-31T00:00:00Z', 'end': '2026-11-02T00:00:00Z'})
    item = response.json()['items'][0]
    assert item['example_event_ids'] == row.related_event_ids
    assert item['evidence']['event_count'] == 2



def test_detail_download_link_exports_historical_finding(db_session, export_client):
    import re
    from html import unescape
    db_session.add(Setting(key='plugin.traefik_log.enabled', value='true'))
    row = finding()
    row.occurred_at = datetime(2020, 1, 1)
    db_session.add(row)
    db_session.commit()
    response = export_client.get(f'/insights/{row.id}')
    assert response.status_code == 200
    link = re.search(r'href="(/api/insights/export\?[^"]+)"', response.text)
    assert link is not None
    download = export_client.get(unescape(link.group(1)))
    assert download.status_code == 200
    assert [item['id'] for item in download.json()['items']] == [row.id]


@pytest.mark.parametrize('evidence', ['unstructured legacy text', {'version': 1, 'kind': ['foreign']},
    {'version': 1, 'kind': 'direct', 'window_start': '0001-01-01T00:00:00+23:59'}])
def test_serializer_handles_unstructured_and_invalid_legacy_evidence(evidence):
    from typing import Any, cast
    insight = finding()
    insight.id = 1
    insight.evidence = cast(Any, evidence)
    insight.confidence = float('inf')
    payload = serialize_insight(insight)
    assert payload['confidence'] == 0
    assert payload['certainty'] == 'possible'
    assert 'window_start' not in payload['evidence']
    json.dumps(payload, allow_nan=False)
