from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import settings as settings_module
from app.models.saved_views import SavedView
from app.models.users import User


def test_migration_copies_legacy_views_to_existing_users_without_overwriting(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'saved-view-migration.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "c2d3e4f5a6b7")

    engine = create_engine(database_url)
    session = sessionmaker(bind=engine)()
    try:
        first_user = User(username="first", password_hash="hash", role="viewer")
        second_user = User(username="second", password_hash="hash", role="operator")
        session.add_all([first_user, second_user])
        session.flush()
        session.add_all(
            [
                SavedView(
                    name="Legacy",
                    scope="events",
                    filter_json={"country": "DE"},
                    query_json={"range": "24h"},
                ),
                SavedView(
                    user_id=first_user.id,
                    name="Legacy",
                    scope="events",
                    filter_json={"country": "US"},
                    query_json={"range": "7d"},
                ),
            ]
        )
        session.commit()
        first_user_id = first_user.id
        second_user_id = second_user.id
    finally:
        session.close()

    command.upgrade(config, "head")

    session = sessionmaker(bind=engine)()
    try:
        first_view = session.query(SavedView).filter(SavedView.user_id == first_user_id).one()
        second_view = session.query(SavedView).filter(SavedView.user_id == second_user_id).one()
        assert (first_view.filter_json, first_view.query_json) == ({"country": "US"}, {"range": "7d"})
        assert (second_view.filter_json, second_view.query_json) == ({"country": "DE"}, {"range": "24h"})
    finally:
        session.close()
        engine.dispose()
