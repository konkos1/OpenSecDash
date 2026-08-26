from datetime import datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.models import CrowdSecAsnBan, CrowdSecAsnBanEnforcement, CrowdSecAsnBanException


def test_asn_ban_metadata_has_required_constraints_and_indexes(db_session):
    inspector = inspect(db_session.bind)

    assert {
        "crowdsec_asn_bans",
        "crowdsec_asn_ban_exceptions",
        "crowdsec_asn_ban_enforcements",
    }.issubset(inspector.get_table_names())
    assert {column["name"] for column in inspector.get_columns("crowdsec_asn_bans")} == {
        "id",
        "asn",
        "provider_name",
        "previous_provider_name",
        "provider_review_required",
        "provider_name_changed_at",
        "provider_reviewed_at",
        "status",
        "created_at",
        "last_matched_at",
        "removal_error",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("crowdsec_asn_ban_exceptions")} == {
        "id",
        "asn_ban_id",
        "ip",
        "created_at",
        "source_action_id",
    }
    assert {column["name"] for column in inspector.get_columns("crowdsec_asn_ban_enforcements")} == {
        "id",
        "asn_ban_id",
        "ip",
        "last_event_id",
        "last_seen_at",
        "action_id",
        "decision_id",
        "decision_until",
        "scenario",
        "last_observed_asn",
        "release_pending",
        "release_error",
        "created_at",
        "updated_at",
    }
    assert {
        item["name"]
        for item in inspector.get_unique_constraints("crowdsec_asn_bans")
    } == {"uq_crowdsec_asn_ban_asn"}
    assert {
        item["name"]
        for item in inspector.get_unique_constraints("crowdsec_asn_ban_exceptions")
    } == {"uq_crowdsec_asn_ban_exception_ip"}
    assert {
        item["name"]
        for item in inspector.get_unique_constraints("crowdsec_asn_ban_enforcements")
    } == {"uq_crowdsec_asn_ban_enforcement_ip"}

    ban_indexes = {item["name"] for item in inspector.get_indexes("crowdsec_asn_bans")}
    assert {
        "ix_crowdsec_asn_bans_asn",
        "ix_crowdsec_asn_bans_provider_review_required",
        "ix_crowdsec_asn_bans_provider_name_changed_at",
        "ix_crowdsec_asn_bans_status",
        "ix_crowdsec_asn_bans_created_at",
        "ix_crowdsec_asn_bans_last_matched_at",
    }.issubset(ban_indexes)
    assert {
        "ix_crowdsec_asn_ban_exceptions_asn_ban_id",
        "ix_crowdsec_asn_ban_exceptions_ip",
        "ix_crowdsec_asn_ban_exceptions_created_at",
    }.issubset({item["name"] for item in inspector.get_indexes("crowdsec_asn_ban_exceptions")})
    assert {
        "ix_crowdsec_asn_ban_enforcements_asn_ban_id",
        "ix_crowdsec_asn_ban_enforcements_ip",
        "ix_crowdsec_asn_ban_enforcements_last_seen_at",
        "ix_crowdsec_asn_ban_enforcements_decision_id",
        "ix_crowdsec_asn_ban_enforcements_decision_until",
        "ix_crowdsec_asn_ban_enforcements_last_observed_asn",
        "ix_crowdsec_asn_ban_enforcements_release_pending",
    }.issubset({item["name"] for item in inspector.get_indexes("crowdsec_asn_ban_enforcements")})

    for table_name in ("crowdsec_asn_ban_exceptions", "crowdsec_asn_ban_enforcements"):
        foreign_keys = inspector.get_foreign_keys(table_name)
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["referred_table"] == "crowdsec_asn_bans"
        assert foreign_keys[0]["options"] == {"ondelete": "CASCADE"}


def test_asn_ban_defaults_create_no_implicit_children(db_session):
    assert db_session.query(CrowdSecAsnBan).count() == 0
    assert db_session.query(CrowdSecAsnBanException).count() == 0
    assert db_session.query(CrowdSecAsnBanEnforcement).count() == 0

    ban = CrowdSecAsnBan(asn="AS64500")
    db_session.add(ban)
    db_session.commit()
    db_session.refresh(ban)

    assert ban.status == "active"
    assert ban.provider_review_required is False
    assert ban.created_at is not None
    assert ban.updated_at is not None
    assert db_session.query(CrowdSecAsnBanException).count() == 0
    assert db_session.query(CrowdSecAsnBanEnforcement).count() == 0


def test_asn_and_per_policy_ip_uniqueness_reject_duplicates(db_session):
    first = CrowdSecAsnBan(asn="AS64501")
    second = CrowdSecAsnBan(asn="AS64502")
    db_session.add_all([first, second])
    db_session.commit()

    db_session.add(CrowdSecAsnBan(asn="AS64501"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(CrowdSecAsnBanException(asn_ban_id=first.id, ip="8.8.8.8"))
    db_session.commit()
    db_session.add(CrowdSecAsnBanException(asn_ban_id=first.id, ip="8.8.8.8"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(CrowdSecAsnBanException(asn_ban_id=second.id, ip="8.8.8.8"))
    db_session.commit()
    db_session.add(CrowdSecAsnBanEnforcement(asn_ban_id=first.id, ip="2001:4860:4860::8888"))
    db_session.commit()
    db_session.add(CrowdSecAsnBanEnforcement(asn_ban_id=first.id, ip="2001:4860:4860::8888"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(CrowdSecAsnBan(asn="AS64506", status="paused"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_deleting_policy_cascades_to_exception_and_enforcement(db_session):
    db_session.execute(text("PRAGMA foreign_keys=ON"))
    db_session.add(CrowdSecAsnBanException(asn_ban_id=999, ip="1.0.0.1"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    ban = CrowdSecAsnBan(asn="AS64503")
    ban.exceptions.append(CrowdSecAsnBanException(ip="1.1.1.1"))
    ban.enforcements.append(
        CrowdSecAsnBanEnforcement(
            ip="1.1.1.1",
            decision_id="decision-1",
            scenario="opensecdash/manual-permanent-asn-ban/AS64503",
        )
    )
    db_session.add(ban)
    db_session.commit()

    db_session.delete(ban)
    db_session.commit()

    assert db_session.query(CrowdSecAsnBan).count() == 0
    assert db_session.query(CrowdSecAsnBanException).count() == 0
    assert db_session.query(CrowdSecAsnBanEnforcement).count() == 0


def test_provider_review_state_preserves_policy_ownership_rows(db_session):
    changed_at = datetime(2026, 8, 24, 10, 30)
    reviewed_at = datetime(2026, 8, 25, 8, 0)
    ban = CrowdSecAsnBan(asn="AS64504", provider_name="Example Network")
    ban.exceptions.append(CrowdSecAsnBanException(ip="9.9.9.9"))
    ban.enforcements.append(CrowdSecAsnBanEnforcement(ip="9.9.9.9", decision_id="decision-2"))
    db_session.add(ban)
    db_session.commit()

    assert ban.provider_review_required is False
    ban.previous_provider_name = ban.provider_name
    ban.provider_name = "Example Transit"
    ban.provider_name_changed_at = changed_at
    ban.provider_review_required = True
    db_session.commit()

    assert ban.previous_provider_name == "Example Network"
    assert ban.provider_name == "Example Transit"
    assert ban.provider_review_required is True
    assert ban.status == "active"
    assert len(ban.exceptions) == 1
    assert len(ban.enforcements) == 1
    assert ban.enforcements[0].decision_id == "decision-2"

    ban.provider_review_required = False
    ban.provider_reviewed_at = reviewed_at
    db_session.commit()

    assert ban.previous_provider_name == "Example Network"
    assert ban.provider_reviewed_at == reviewed_at
    assert ban.status == "active"
    assert ban.exceptions[0].ip == "9.9.9.9"
    assert ban.enforcements[0].decision_id == "decision-2"
