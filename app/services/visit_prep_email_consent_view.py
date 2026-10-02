"""Audited current email choice for a confirmed bearer, never send authority."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from app.services.visit_prep_contributions import _require_recipient
from app.services.visit_prep_email_consent import (
    _ERRORS,
    CONSENT_DISCLOSURE_VERSION,
    _capture_context,
    _refs,
    email_consent_disclosure,
    email_consent_disclosure_fingerprint,
    read_patient_email_consent_source,
    require_patient_email_consent_control,
)
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_evidence import read_email_consent
from app.services.visit_prep_packages import _database_now
from app.services.visit_prep_patient_continuation import (
    ConfirmedVisitPrepAccess,
    valid_confirmation_name,
)
from app.services.visit_preps import _visit_prep_session

from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_email_authority import VisitPrepEmailConsentEvent
from sqlmodel import Session, select

ConsentStatus = Literal["not_recorded", "grant_recorded", "withdrawn", "expired"]
RecipientRole = Literal["patient", "caregiver"]


@dataclass(frozen=True, repr=False)
class PatientEmailConsentView:
    actor_role: RecipientRole
    disclosure_version: str
    grant_disclosure: str
    grant_disclosure_fingerprint: str
    withdrawal_disclosure: str
    withdrawal_disclosure_fingerprint: str
    status: ConsentStatus
    event_sequence: int
    recorded_at: datetime | None
    expires_at: datetime | None
    maximum_expires_at: datetime
    can_grant: bool
    checked_at: datetime

    def __repr__(self) -> str:
        return "PatientEmailConsentView(<redacted>)"


def _read(
    session: Session,
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: RecipientRole,
) -> PatientEmailConsentView:
    require_patient_email_consent_control()
    authorized = _capture_context(session, access_token=access_token, role=role)
    _require_recipient(authorized, first_name=first_name, role=role)
    prep = authorized.visit_prep
    row = session.exec(
        select(VisitPrepEmailConsentEvent)
        .where(VisitPrepEmailConsentEvent.visit_prep_id == prep.id)
        .order_by(cast(Any, VisitPrepEmailConsentEvent.event_sequence).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    evidence = read_email_consent(row) if row is not None else None
    source = (
        read_patient_email_consent_source(session, evidence)
        if evidence is not None
        else None
    )
    grant_hash = email_consent_disclosure_fingerprint(role=role, event_kind="granted")
    withdrawal_hash = email_consent_disclosure_fingerprint(
        role=role, event_kind="withdrawn"
    )
    grant_text = email_consent_disclosure(role=role, event_kind="granted")
    withdrawal_text = email_consent_disclosure(role=role, event_kind="withdrawn")
    session.add(
        AuthorityAuditEvent(
            event_type="visit_prep.email_consent_read",
            outcome="allowed",
            occurred_at=_database_now(session),
            recorded_at=_database_now(session),
            resource_type="visit_prep",
            resource_ref=str(prep.id),
            content_sha256=grant_hash,
            route="visit_prep_patient_email_consent_read",
            intent="confirmed_bearer_email_choice_review",
            platform_refs=_refs(prep, authorized.credential),
        )
    )
    session.flush()
    checked = _capture_context(session, access_token=access_token, role=role)
    require_patient_email_consent_control()
    _require_recipient(checked, first_name=first_name, role=role)
    now = checked.event_at
    if grant_hash != email_consent_disclosure_fingerprint(
        role=role, event_kind="granted"
    ) or withdrawal_hash != email_consent_disclosure_fingerprint(
        role=role, event_kind="withdrawn"
    ):
        raise VisitPrepEmailInvalid()
    if evidence is not None:
        assert row is not None and source is not None
        audit = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_type == "visit_prep_email_consent",
                AuthorityAuditEvent.resource_ref == evidence.consent_event_id,
            )
        ).one()
        if (
            evidence.actor_role != role
            or evidence.effective_at > now
            or row.recorded_at > now
            or audit.recorded_at > now
        ):
            raise VisitPrepEmailInvalid()
    maximum = min(checked.credential.expires_at, checked.schedule.scheduled_at)
    status: ConsentStatus = "not_recorded"
    if evidence is not None:
        if evidence.event_kind == "withdrawn":
            status = "withdrawn"
        elif evidence.expires_at is None or min(evidence.expires_at, maximum) <= now:
            status = "expired"
        else:
            status = "grant_recorded"
    result = PatientEmailConsentView(
        role,
        CONSENT_DISCLOSURE_VERSION,
        grant_text,
        grant_hash,
        withdrawal_text,
        withdrawal_hash,
        status,
        evidence.event_sequence if evidence is not None else 0,
        row.recorded_at if row is not None else None,
        evidence.expires_at if evidence is not None else None,
        maximum,
        prep.completed_at is None and maximum > now,
        now,
    )
    session.commit()
    return result


def get_patient_email_consent_view(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: RecipientRole,
    view_as_active: bool = False,
) -> PatientEmailConsentView:
    """Read the latest choice with fresh confirmation; no contact/answer exposure.

    Recorded grants remain unqualified capture evidence. No sender resolver,
    patient identity, channel verification, or first-email authority is created.
    """
    result = None
    try:
        if (
            not valid_confirmation_name(first_name, access_token)
            or role not in {"patient", "caregiver"}
            or view_as_active is not False
        ):
            raise VisitPrepEmailInvalid()
        with _visit_prep_session() as session:
            try:
                result = _read(
                    session, access_token=access_token, first_name=first_name, role=role
                )
            except _ERRORS:
                session.rollback()
                raise
    except _ERRORS:
        pass
    if result is None:
        raise VisitPrepEmailInvalid()
    return result
