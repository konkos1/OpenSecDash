"""Startup seeding of auth activation and onboarding state (see plan phase 1)."""

import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.init_db import seed_defaults
from app.models.settings import Setting
from app.services.auth import (
    AUTH_ENABLED_SETTING,
    AUTH_ONBOARDING_COMPLETE,
    AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED,
    AUTH_ONBOARDING_PENDING,
    AUTH_ONBOARDING_STATE_SETTING,
    onboarding_required,
    onboarding_review_required,
    onboarding_state,
)


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield factory
    finally:
        engine.dispose()


def _value(db, key):
    setting = db.query(Setting).filter(Setting.key == key).first()
    return setting.value if setting is not None else None


def test_empty_database_seeds_enabled_and_pending(session_factory):
    db = session_factory()
    seed_defaults(db)
    assert _value(db, AUTH_ENABLED_SETTING) == "true"
    assert _value(db, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_PENDING
    assert onboarding_required(db) is True
    db.close()


def test_second_seed_is_idempotent(session_factory):
    db = session_factory()
    seed_defaults(db)
    seed_defaults(db)
    assert _value(db, AUTH_ENABLED_SETTING) == "true"
    assert _value(db, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_PENDING
    db.close()


def test_failed_seed_before_commit_leaves_no_partial_auth_state(session_factory):
    db = session_factory()

    def boom():
        raise RuntimeError("commit failed")

    db.commit = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="commit failed"):
        seed_defaults(db)
    db.rollback()
    db.close()

    verify = session_factory()
    assert verify.query(Setting).count() == 0
    assert _value(verify, AUTH_ENABLED_SETTING) is None
    assert _value(verify, AUTH_ONBOARDING_STATE_SETTING) is None
    verify.close()


def test_existing_install_without_auth_key_is_legacy_review(session_factory):
    db = session_factory()
    db.add(Setting(key="language", value="en"))
    db.commit()
    seed_defaults(db)
    assert _value(db, AUTH_ENABLED_SETTING) == "false"
    assert _value(db, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED
    assert onboarding_required(db) is False
    assert onboarding_review_required(db) is True
    db.close()


def test_existing_enabled_install_becomes_complete(session_factory):
    db = session_factory()
    db.add(Setting(key=AUTH_ENABLED_SETTING, value="true"))
    db.commit()
    seed_defaults(db)
    assert _value(db, AUTH_ENABLED_SETTING) == "true"
    assert _value(db, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_COMPLETE
    db.close()


def test_existing_disabled_install_becomes_legacy_review(session_factory):
    db = session_factory()
    db.add(Setting(key=AUTH_ENABLED_SETTING, value="false"))
    db.commit()
    seed_defaults(db)
    assert _value(db, AUTH_ENABLED_SETTING) == "false"
    assert _value(db, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED
    db.close()


def test_existing_onboarding_state_is_never_overwritten(session_factory):
    db = session_factory()
    db.add_all(
        [
            Setting(key=AUTH_ENABLED_SETTING, value="true"),
            Setting(key=AUTH_ONBOARDING_STATE_SETTING, value=AUTH_ONBOARDING_PENDING),
        ]
    )
    db.commit()
    seed_defaults(db)
    # Classification would say "complete" here, but a stored state is authoritative.
    assert _value(db, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_PENDING
    db.close()


def test_unknown_onboarding_state_is_logged_and_not_normalized(session_factory, caplog):
    db = session_factory()
    db.add(Setting(key=AUTH_ONBOARDING_STATE_SETTING, value="tampered"))
    db.commit()
    with caplog.at_level(logging.ERROR, logger="app.services.auth"):
        assert onboarding_state(db) == AUTH_ONBOARDING_COMPLETE
    assert onboarding_required(db) is False
    assert onboarding_review_required(db) is False
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert "tampered" not in caplog.text
    db.close()
