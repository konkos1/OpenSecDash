"""Exercise upgrade and default preservation across the complete Insight rollout."""
from datetime import datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.orm import Session

from app.core import settings as settings_module
from app.database.init_db import seed_defaults
from app.models.core import Insight, NotificationRule
from app.models.settings import Setting
from app.plugins.manager import get_plugin_manager
from app.services.insight_rules import import_bundled_rules


@pytest.mark.parametrize('fresh', [False, True])
def test_final_upgrade_preserves_data_choices_and_disabled_defaults(tmp_path, monkeypatch, fresh):
    url = f"sqlite:///{tmp_path / 'rollout.db'}"
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setattr(settings_module.settings, 'database_url', url)
    config = Config('alembic.ini')
    engine = create_engine(url)
    if not fresh:
        command.upgrade(config, 'e7f8a9b0c1d2')
        settings = Table('settings', MetaData(), autoload_with=engine)
        insights = Table('insights', MetaData(), autoload_with=engine)
        with engine.begin() as connection:
            connection.execute(settings.insert().values(key='language', value='de'))
            connection.execute(settings.insert().values(key='plugin.mqtt-hass.publish_insights', value='false'))
            connection.execute(insights.insert().values(id=42, type='legacy', title='Legacy',
                description='Retained text', timestamp=datetime(2020, 1, 1), level='high', confidence=.9))
    command.upgrade(config, 'head')
    with Session(engine) as db:
        seed_defaults(db)
        get_plugin_manager().seed_database(db)
        import_bundled_rules(db)
        db.commit()
        assert db.query(Setting).filter_by(key='plugin.mqtt-hass.publish_insights').one().value == 'false'
        assert db.query(NotificationRule).filter(NotificationRule.enabled.is_(True)).count() == 0
        if not fresh:
            row = db.get(Insight, 42)
            assert row is not None and row.description == 'Retained text'
            assert row.occurred_at is None and row.evidence is None
            assert db.query(Setting).filter_by(key='language').one().value == 'de'
        choice = db.query(NotificationRule).first()
        assert choice is not None
        choice.enabled = True
        db.commit()
        choice_id = choice.id
        get_plugin_manager().seed_database(db)
        import_bundled_rules(db)
        db.commit()
        saved = db.get(NotificationRule, choice_id)
        assert saved is not None and saved.enabled is True
    engine.dispose()
