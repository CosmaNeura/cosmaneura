import secrets
from datetime import datetime, timezone

import pytest

from app.services.public_legal import CURRENT_LEGAL_DOCUMENTS

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.public_onboarding import CredentialReviewAudit, PublicLegalAcceptance
from sqlalchemy.exc import DBAPIError
from sqlmodel import select


def _public_practice() -> Practice:
    return Practice(
        legal_name="Credential Boundary Practice",
        join_code=f"C{secrets.token_hex(4).upper()[:7]}",
        email_domain=f"credential-{secrets.token_hex(4)}.example.com",
        origin_channel="direct",
        public_onboarding_required=True,
    ).save()


def _doctor(email: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_{secrets.token_hex(8)}",
        email=email,
        biography="",
    ).save()


def test_database_forbids_npi_on_public_practices_but_preserves_legacy_storage() -> (
    None
):
    public_practice = _public_practice()
    legacy_managed = Practice(
        legal_name="Legacy Managed NPI Practice",
        npi="1234567893",
        join_code=f"M{secrets.token_hex(4).upper()[:7]}",
        email_domain=f"managed-{secrets.token_hex(4)}.example.com",
        origin_channel="mcd",
        public_onboarding_required=False,
    ).save()

    with pytest.raises(DBAPIError, match="ck_practice_public_onboarding_no_npi"):
        with get_session() as session:
            locked = session.exec(
                select(Practice)
                .where(Practice.id == public_practice.id)
                .with_for_update()
            ).one()
            locked.npi = "1234567893"
            session.add(locked)
            session.commit()

    public_practice.refresh()
    legacy_managed.refresh()
    assert public_practice.npi is None
    assert legacy_managed.npi == "1234567893"


def test_database_rejects_preapproved_membership_insert() -> None:
    practice = _public_practice()
    clinician = _doctor(f"clinician@{practice.email_domain}")

    with pytest.raises(DBAPIError, match="must begin with pending credentials"):
        with get_session() as session:
            session.add(
                PracticeMember(
                    practice_id=practice.id,
                    doctor_id=clinician.id,
                    role="workforce",
                    credential_state="approved",
                    credential_reviewed_at=datetime.now(timezone.utc),
                    credential_reviewer_ref="unconfigured-reviewer",
                    credential_verification_method="unconfigured-method",
                    credential_evidence_sha256="a" * 64,
                )
            )
            session.commit()

    assert PracticeMember.get(doctor_id=clinician.id) is None


def test_database_rejects_credential_transition_without_audit() -> None:
    practice = _public_practice()
    clinician = _doctor(f"clinician@{practice.email_domain}")
    member = PracticeMember(
        practice_id=practice.id,
        doctor_id=clinician.id,
        role="workforce",
    ).save()
    reviewed_at = datetime.now(timezone.utc)

    with pytest.raises(DBAPIError, match="matching audit evidence"):
        with get_session() as session:
            locked = session.exec(
                select(PracticeMember)
                .where(PracticeMember.id == member.id)
                .with_for_update()
            ).one()
            locked.credential_state = "approved"
            locked.credential_reviewed_at = reviewed_at
            locked.credential_reviewer_ref = "unconfigured-reviewer"
            locked.credential_verification_method = "unconfigured-method"
            locked.credential_evidence_sha256 = "b" * 64
            session.add(locked)
            session.commit()

    member.refresh()
    assert member.credential_state == "pending"


def test_database_rejects_self_review_audit() -> None:
    practice = _public_practice()
    clinician = _doctor(f"clinician@{practice.email_domain}")
    member = PracticeMember(
        practice_id=practice.id,
        doctor_id=clinician.id,
        role="owner",
    ).save()
    reviewed_at = datetime.now(timezone.utc)

    with pytest.raises(DBAPIError, match="cannot approve their own credentials"):
        with get_session() as session:
            session.add(
                CredentialReviewAudit(
                    practice_member_id=member.id,
                    from_state="pending",
                    to_state="approved",
                    reviewer_ref="self-review-attempt",
                    reviewer_doctor_id=clinician.id,
                    verification_method="unconfigured-method",
                    evidence_sha256="c" * 64,
                    reason_code="self-review-attempt",
                    reviewed_at=reviewed_at,
                    event_digest="d" * 64,
                )
            )
            session.commit()


def test_database_rejects_unconfigured_independent_reviewer() -> None:
    practice = _public_practice()
    clinician = _doctor(f"clinician@{practice.email_domain}")
    reviewer = _doctor(f"reviewer@{practice.email_domain}")
    member = PracticeMember(
        practice_id=practice.id,
        doctor_id=clinician.id,
        role="workforce",
    ).save()

    with pytest.raises(DBAPIError, match="reviewer authorization is not configured"):
        with get_session() as session:
            session.add(
                CredentialReviewAudit(
                    practice_member_id=member.id,
                    from_state="pending",
                    to_state="approved",
                    reviewer_ref="unconfigured-reviewer",
                    reviewer_doctor_id=reviewer.id,
                    verification_method="unconfigured-method",
                    evidence_sha256="e" * 64,
                    reason_code="review-not-enabled",
                    reviewed_at=datetime.now(timezone.utc),
                    event_digest="f" * 64,
                )
            )
            session.commit()

    member.refresh()
    assert member.credential_state == "pending"


def test_database_rejects_legal_acceptance_by_non_owner() -> None:
    practice = _public_practice()
    owner = _doctor(f"owner@{practice.email_domain}")
    workforce = _doctor(f"workforce@{practice.email_domain}")
    PracticeMember(
        practice_id=practice.id,
        doctor_id=owner.id,
        role="owner",
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=workforce.id,
        role="workforce",
    ).save()
    document = CURRENT_LEGAL_DOCUMENTS[0]

    with pytest.raises(DBAPIError, match="active practice owner"):
        with get_session() as session:
            session.add(
                PublicLegalAcceptance(
                    practice_id=practice.id,
                    document_kind=document.kind.value,
                    document_version=document.version,
                    content_sha256=document.sha256,
                    signer_doctor_id=workforce.id,
                    signer_email=workforce.email,
                    signer_role="owner",
                    signature_name="Synthetic Workforce",
                    signature_title="Clinician",
                    authority_confirmed=True,
                    signer_authority="Invalid direct database attempt",
                    accepted_at_utc=datetime.now(timezone.utc),
                )
            )
            session.commit()
