"""Real synthetic PostgreSQL package reads, provenance and current authority."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services import visit_prep_access
from app.services.visit_prep_contributions import (
    correct_contribution,
    review_contribution,
)
from app.services.visit_prep_interview_contract import ResponseInput
from app.services.visit_prep_interviews import report_wrong_recipient
from app.services.visit_prep_packages import get_visit_prep_package, list_visit_preps
from app.services.visit_preps import (
    RecipientInput,
    VisitPrepNotFound,
    VisitPrepValidation,
    create_visit_prep,
    revoke_visit_prep,
    rotate_visit_prep_credential,
)

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import VisitPrepCredentialGeneration
from sqlmodel import select

from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_interviews_test import _fixture
from tests.services.visit_preps_test import _withdraw_seat


def _scope(fixture, *, package=True):
    scope = dict(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
    )
    if package:
        scope["expected_clerk_subject"] = fixture.doctor.clerk_id
    return scope


def _read(fixture):
    return get_visit_prep_package(**_scope(fixture), visit_prep_id=fixture.prep_id)


def test_package_is_a_stable_unlinked_patient_source_and_audited(monkeypatch):
    fixture = _prepared(monkeypatch, "provider-package")
    first, second = _read(fixture), _read(fixture)
    assert first.source_version == second.source_version
    assert (
        first.preparation.patient_id is None
        and first.preparation.appointment_id is None
    )
    assert first.contribution.current_answers_complete
    assert not first.contribution.current_answers_reviewed
    assert "invented" not in repr(first)
    with get_session() as session:
        audits = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                AuthorityAuditEvent.event_type == "visit_prep.package_read",
            )
        ).all()
    assert len(audits) == 2
    assert all(
        event.intent == "provider_previsit_review"
        and event.request_digest == first.source_version
        for event in audits
    )
    assert all(
        "invented" not in repr(event.platform_refs)
        and fixture.access_token not in repr(event)
        for event in audits
    )


def test_correction_and_review_each_change_the_exact_source_version(monkeypatch):
    fixture = _prepared(monkeypatch, "provider-package-revision")
    first = _read(fixture)
    receipt = correct_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        expected_basis=first.contribution.basis,
        idempotency_key=str(uuid4()),
        response=ResponseInput("story", "answered", "My revised synthetic story."),
    )
    second = _read(fixture)
    assert first.source_version != second.source_version
    story = next(
        topic for topic in second.contribution.topics if topic.question.key == "story"
    )
    assert (
        story.answer == "My revised synthetic story."
        and story.original_answer == "My invented health story."
    )
    review_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        expected_basis=receipt.basis,
        idempotency_key=str(uuid4()),
    )
    third = _read(fixture)
    assert third.source_version not in {first.source_version, second.source_version}
    assert third.contribution.current_answers_reviewed


def test_partial_and_caregiver_packages_never_claim_complete_review(monkeypatch):
    fixture = _prepared(
        monkeypatch, "provider-package-partial", role="caregiver", complete=False
    )
    package = _read(fixture)
    assert package.contribution.actor_role == "caregiver"
    assert not package.contribution.current_answers_complete
    assert not package.contribution.original_interview_completed
    assert not package.contribution.current_answers_reviewed
    assert package.contribution.missing_question_keys == (
        "medications",
        "adherence",
        "story",
    )


def test_owner_read_is_independent_of_public_gate_and_expired_patient_invitation(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "provider-package-expiry")
    before = _read(fixture)
    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(visit_prep_access.VISIT_PREP_SETTINGS, public_api_enabled=False),
    )
    with get_session() as session:
        credential = session.exec(
            select(VisitPrepCredentialGeneration).where(
                VisitPrepCredentialGeneration.visit_prep_id == fixture.prep_id
            )
        ).one()
    # Advance the database-owned read clock, not immutable credentials.
    import app.services.visit_prep_packages as service

    monkeypatch.setattr(
        service,
        "_database_now",
        lambda _session: credential.expires_at + timedelta(hours=1),
    )
    after = _read(fixture)
    assert after.source_version == before.source_version
    assert not after.preparation.credential_active


@pytest.mark.parametrize(
    "denial",
    [
        "gate",
        "configuration",
        "cohort",
        "baa",
        "entitlement",
        "seat",
        "doctor",
        "revocation",
        "wrong_recipient",
    ],
)
def test_current_authority_and_quarantine_deny_before_disclosure(monkeypatch, denial):
    fixture = _prepared(monkeypatch, f"package-deny-{denial}")
    if denial in {"gate", "configuration", "cohort"}:
        change = {
            "gate": dict(enabled=False),
            "configuration": dict(configuration_valid=False),
            "cohort": dict(enabled_doctor_ids=frozenset()),
        }[denial]
        monkeypatch.setattr(
            visit_prep_access,
            "VISIT_PREP_SETTINGS",
            replace(visit_prep_access.VISIT_PREP_SETTINGS, **change),
        )
    elif denial == "baa":
        fixture.practice.negotiated_baa_on_file = False
        fixture.practice.save()
    elif denial == "entitlement":
        fixture.practice.billing_exempt = False
        fixture.practice.save()
    elif denial == "seat":
        _withdraw_seat(fixture.owner)
    elif denial == "doctor":
        fixture.doctor.deleted_at = datetime.now(UTC)
        fixture.doctor.save()
    elif denial == "wrong_recipient":
        report_wrong_recipient(access_token=fixture.access_token)
    else:
        revoke_visit_prep(
            **_scope(fixture, package=False),
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
            reason="physician_revoked",
        )
    with pytest.raises(VisitPrepNotFound):
        _read(fixture)
    with get_session() as session:
        assert not session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                AuthorityAuditEvent.event_type == "visit_prep.package_read",
            )
        ).all()


def test_wrong_owner_cannot_read_package_or_use_another_owners_cursor(monkeypatch):
    first = _prepared(monkeypatch, "package-owner-first")
    second = _prepared(monkeypatch, "package-owner-second")
    with pytest.raises(VisitPrepNotFound):
        get_visit_prep_package(**_scope(second), visit_prep_id=first.prep_id)
    with pytest.raises(VisitPrepNotFound):
        list_visit_preps(**_scope(second), before_id=first.prep_id)


def test_changed_external_identity_denies_a_prelock_authorization_snapshot(monkeypatch):
    fixture = _prepared(monkeypatch, "package-identity-rebind")
    old_scope = _scope(fixture)
    with get_session() as session:
        from app.models.doctor import Doctor

        doctor = session.get(Doctor, fixture.doctor.id)
        doctor.clerk_id = f"user_rebound_{uuid4().hex}"
        session.add(doctor)
        session.commit()
    with pytest.raises(VisitPrepNotFound):
        get_visit_prep_package(**old_scope, visit_prep_id=fixture.prep_id)
    with pytest.raises(VisitPrepNotFound):
        list_visit_preps(**old_scope)


def test_unconfirmed_invitation_is_listable_but_has_no_disclosable_answers(monkeypatch):
    fixture = _fixture(monkeypatch, "package-unconfirmed")
    listing = list_visit_preps(**_scope(fixture))
    assert [prep.id for prep in listing.preparations] == [fixture.prep_id]
    with pytest.raises(VisitPrepNotFound):
        _read(fixture)


def test_real_pagination_is_stable_bounded_and_has_no_duplicate_records(monkeypatch):
    fixture = _fixture(monkeypatch, "package-pagination")
    ids = [fixture.prep_id]
    with get_session() as session:
        from app.models.visit_prep import VisitPrep

        version_id = session.get(VisitPrep, fixture.prep_id).question_set_version_id
    for _ in range(3):
        created = create_visit_prep(
            **_scope(fixture, package=False),
            idempotency_key=str(uuid4()),
            recipient=RecipientInput(
                first_name="Synthetic", role="patient", email="synthetic@example.com"
            ),
            physician_display_name="Dr. Synthetic",
            scheduled_at=fixture.scheduled_at,
            timezone_name="UTC",
            question_set_version_id=version_id,
        )
        ids.append(created.visit_prep.id)
    first = list_visit_preps(**_scope(fixture), limit=2)
    assert [prep.id for prep in first.preparations] == list(reversed(ids))[:2]
    assert first.next_before_id == first.preparations[-1].id
    second = list_visit_preps(
        **_scope(fixture), limit=2, before_id=first.next_before_id
    )
    assert [prep.id for prep in second.preparations] == list(reversed(ids))[2:]
    assert second.next_before_id is None


def test_rotation_changes_source_context_but_preserves_answers(monkeypatch):
    fixture = _prepared(monkeypatch, "package-rotate", complete=False)
    before = _read(fixture)
    rotate_visit_prep_credential(
        **_scope(fixture, package=False),
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    after = _read(fixture)
    assert before.source_version != after.source_version
    assert before.contribution == after.contribution


@pytest.mark.parametrize("limit", [0, 51, True, "20"])
def test_list_limits_are_bounded(limit):
    with pytest.raises(VisitPrepValidation):
        list_visit_preps(
            owner_practice_member_id=None,
            owner_doctor_id=None,
            practice_id=None,
            expected_clerk_subject="synthetic",
            limit=limit,
        )
