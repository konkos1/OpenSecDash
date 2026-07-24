"""Atomic completion of the first-admin onboarding.

The whole completion runs in one transaction that starts by claiming the open
onboarding state. Two first visitors therefore serialize on the database write
lock instead of a process-local lock, and the loser gets a harmless "already
completed" answer rather than a second administrator.
"""
import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.settings import Setting
from app.models.users import User
from app.services.auth import (
    AUTH_ENABLED_SETTING,
    AUTH_HOSTNAME_SETTING,
    AUTH_ONBOARDING_COMPLETE,
    AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED,
    AUTH_ONBOARDING_PENDING,
    AUTH_ONBOARDING_STATE_SETTING,
    active_admin_count,
    create_user,
    validate_new_user,
)
from app.web.tables import save_setting

logger = logging.getLogger(__name__)

# Only ever visible inside the uncommitted claiming transaction, never persisted
# on its own: a failure rolls the claim back together with everything else.
AUTH_ONBOARDING_CLAIMING = "claiming"
_CLAIMABLE_STATES = (AUTH_ONBOARDING_PENDING, AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED)

ERROR_ALREADY_COMPLETED = "already_completed"
ERROR_INCONSISTENT_STATE = "inconsistent_state"
ERROR_SETUP_FAILED = "setup_failed"


def store_activation(db: Session, hostname: str) -> None:
    """Persist the settings every activation shares, without committing.

    The onboarding completion and the settings activation of an upgraded open
    installation write exactly the same three values, so both end in a state
    where only ``OSD_AUTH_DISABLED`` can still bypass internal sign-in.
    """
    save_setting(db, AUTH_HOSTNAME_SETTING, hostname)
    save_setting(db, AUTH_ENABLED_SETTING, "true")
    save_setting(db, AUTH_ONBOARDING_STATE_SETTING, AUTH_ONBOARDING_COMPLETE)


def account_required(db: Session, state: str) -> bool:
    """Return whether the completion has to create the first administrator.

    A new installation always creates one. An upgraded open installation only
    does so when no active admin is left from its earlier use; an existing one
    is reused without exposing or changing it.
    """
    return state == AUTH_ONBOARDING_PENDING or active_admin_count(db) == 0


def _claim_open_onboarding(db: Session) -> str | None:
    """Claim the open state and return which one was claimed, if any."""
    for state in _CLAIMABLE_STATES:
        claimed = (
            db.query(Setting)
            .filter(Setting.key == AUTH_ONBOARDING_STATE_SETTING, Setting.value == state)
            .update({Setting.value: AUTH_ONBOARDING_CLAIMING}, synchronize_session=False)
        )
        if claimed == 1:
            return state
    return None


def complete_onboarding(
    db: Session,
    *,
    hostname: str,
    language: str = "",
    username: str = "",
    password: str = "",
) -> str | None:
    """Complete the onboarding in one transaction, or return an error code.

    The caller has already validated the input and the trusted proxy boundary;
    this function owns everything that writes. No session is created: the first
    administrator signs in through the normal login afterwards. The settings
    activation of a running installation passes no language and keeps its own.
    """
    try:
        claimed_state = _claim_open_onboarding(db)
        if claimed_state is None:
            db.rollback()
            return ERROR_ALREADY_COMPLETED
        if claimed_state == AUTH_ONBOARDING_PENDING and db.query(User).first() is not None:
            # A pending installation with accounts is inconsistent. Adopting or
            # picking one of them would hand out an unclaimed admin, so this
            # fails closed and keeps the state open for a deliberate recovery.
            logger.error("Onboarding is pending although users already exist; refusing to create a first admin")
            db.rollback()
            return ERROR_INCONSISTENT_STATE
        creates_admin = account_required(db, claimed_state)
        if creates_admin:
            error = validate_new_user(db, username, password)
            if error is not None:
                db.rollback()
                return error
        # The global language is stored before the account, so the new
        # administrator's personal preferences start in the language of this
        # form and the following login looks the same as the setup.
        if language:
            save_setting(db, "language", language)
        if creates_admin:
            create_user(db, username, password, "admin")
        store_activation(db, hostname)
        db.commit()
    except SQLAlchemyError:
        logger.exception("Onboarding completion failed; the installation stays in its previous state")
        db.rollback()
        return ERROR_SETUP_FAILED
    return None
