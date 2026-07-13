from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.core import settings as settings_module
from app.core.template_context import build_template_context
from app.models.settings import Setting
from app.models.users import User, UserPreference
from app.services.auth import create_user


def test_migration_backfills_one_normalized_preference_row_per_existing_user(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'preferences-migration.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "a9b8c7d6e5f4")

    engine = create_engine(database_url)
    session = sessionmaker(bind=engine)()
    try:
        session.add_all(
            [
                User(username="first", password_hash="hash", role="viewer"),
                User(username="second", password_hash="hash", role="operator"),
                Setting(key="language", value="de"),
                Setting(key="live_default", value="invalid"),
                Setting(key="theme", value="light"),
                Setting(key="instance_accent_color", value="red"),
                Setting(key="live_page_refresh", value="false"),
            ]
        )
        session.commit()
    finally:
        session.close()

    command.upgrade(config, "head")

    session = sessionmaker(bind=engine)()
    try:
        preferences = session.query(UserPreference).order_by(UserPreference.user_id).all()
        assert [(item.language, item.live_default, item.theme, item.accent_color, item.live_page_refresh) for item in preferences] == [
            ("de", "true", "light", "red", "false"),
            ("de", "true", "light", "red", "false"),
        ]
    finally:
        session.close()

    command.downgrade(config, "b0c1d2e3f4a5")
    assert "user_preferences" not in inspect(engine).get_table_names()
    engine.dispose()


def test_new_user_preferences_use_current_global_defaults(db_session):
    db_session.add_all(
        [
            Setting(key="language", value="de"),
            Setting(key="live_default", value="false"),
            Setting(key="theme", value="dark"),
            Setting(key="instance_accent_color", value="orange"),
            Setting(key="live_page_refresh", value="false"),
        ]
    )
    user = create_user(db_session, "viewer", "password123", "viewer")
    db_session.commit()

    preferences = db_session.query(UserPreference).filter(UserPreference.user_id == user.id).one()
    assert (preferences.language, preferences.live_default, preferences.theme, preferences.accent_color, preferences.live_page_refresh) == (
        "de",
        "false",
        "dark",
        "orange",
        "false",
    )


def test_template_context_uses_each_users_preferences_and_anonymous_defaults(db_session):
    db_session.add_all(
        [
            Setting(key="language", value="en"),
            Setting(key="live_default", value="true"),
            Setting(key="theme", value="auto"),
            Setting(key="instance_accent_color", value="blue"),
            Setting(key="live_page_refresh", value="true"),
        ]
    )
    first_user = create_user(db_session, "first", "password123", "viewer")
    second_user = create_user(db_session, "second", "password123", "viewer")
    db_session.flush()
    first_preferences = db_session.query(UserPreference).filter(UserPreference.user_id == first_user.id).one()
    first_preferences.language = "de"
    first_preferences.live_default = "false"
    first_preferences.theme = "dark"
    first_preferences.accent_color = "red"
    first_preferences.live_page_refresh = "false"
    second_preferences = db_session.query(UserPreference).filter(UserPreference.user_id == second_user.id).one()
    second_preferences.language = "en"
    second_preferences.live_default = "true"
    second_preferences.theme = "light"
    second_preferences.accent_color = "green"
    second_preferences.live_page_refresh = "true"
    db_session.commit()

    first_context = build_template_context(db_session, first_user)
    second_context = build_template_context(db_session, second_user)
    anonymous_context = build_template_context(db_session)

    assert tuple(first_context[key] for key in ("language", "live_default", "theme", "accent_color", "live_page_refresh")) == (
        "de",
        "false",
        "dark",
        "red",
        False,
    )
    assert tuple(second_context[key] for key in ("language", "live_default", "theme", "accent_color", "live_page_refresh")) == (
        "en",
        "true",
        "light",
        "green",
        True,
    )
    assert tuple(anonymous_context[key] for key in ("language", "live_default", "theme", "accent_color", "live_page_refresh")) == (
        "en",
        "true",
        "auto",
        "blue",
        True,
    )
