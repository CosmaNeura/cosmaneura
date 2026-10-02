"""Patient-facing Visit Prep interview domain operations.

The bearer is only a locator.  Every operation reacquires the authoritative
practice, aggregate, schedule, and credential rows in a stable lock order,
then rechecks rollout eligibility and expiry using database time.  Public
results intentionally contain no canonical or patient identifiers and never
return prior answer plaintext.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4

import app.services.visit_prep_access as visit_prep_access
from app.services.doctor_access import doctor_tombstone
from app.services.organization_access import member_authorization_basis
from app.services.visit_prep_interview_contract import (
    CLINICAL_RETENTION_POLICY_VERSION,
    CLINICAL_RETENTION_YEARS,
    InterviewChoice,
    InterviewQuestion,
    QuestionKind,
    ResponseKind,
    _answer_envelope,
    _calendar_years_after,
    _canonical_idempotency_key,
    _CanonicalResponse,
    _normalized_answer,
    _normalized_confirmation_name,
    _response_payload_hmac,
    _validated_response_input,
)
from app.services.visit_prep_interview_contract import (
    InterviewMutationResult as InterviewMutationResult,
)
from app.services.visit_prep_interview_contract import (
    InterviewState as InterviewState,
)
from app.services.visit_prep_interview_contract import (
    ResponseInput as ResponseInput,
)
from app.services.visit_prep_interview_contract import (
    VisitPrepInterviewConflict as VisitPrepInterviewConflict,
)
from app.services.visit_prep_interview_contract import (
    VisitPrepInterviewError as VisitPrepInterviewError,
)
from app.services.visit_prep_interview_contract import (
    VisitPrepInterviewUnavailable as VisitPrepInterviewUnavailable,
)
from app.services.visit_prep_interview_contract import (
    VisitPrepInterviewValidation as VisitPrepInterviewValidation,
)
from app.services.visit_prep_open_evidence import (
    InvitationOpenEvidenceInvalid,
    stage_first_flow_open,
)
from app.services.visit_prep_patient_continuation import ConfirmedVisitPrepAccess
from app.services.visit_prep_question_definitions import VisitPrepQuestionSetValidation
from app.services.visit_prep_question_sets import _definition_from_rows
from app.services.visit_prep_reschedule_continuity import validate_reschedule_continuity
from app.services.visit_preps import (
    VisitPrepValidation as VisitPrepCredentialValidation,
)
from app.services.visit_preps import access_token_hash
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.organization_access import (
    OrganizationRosterApproval,
    ProviderOrganization,
)
from app.models.practice import (
    SEAT_HOLDING_ACCESS_STATES,
    Practice,
    PracticeMember,
    member_access_state,
)
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from app.models.visit_prep_delivery_link import VisitPrepDeliveryLink
from app.models.visit_prep_question_set import (
    VisitPrepQuestion,
    VisitPrepQuestionChoice,
    VisitPrepQuestionSetVersion,
)
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

_LOCK_NOT_AVAILABLE_SQLSTATE = "55P03"


def public_interview_api_enabled() -> bool:
    """Return the independent fail-closed public-surface release decision."""

    settings = visit_prep_access.VISIT_PREP_SETTINGS
    return (
        settings.configuration_valid
        and settings.enabled
        and settings.public_api_enabled
    )


@contextmanager
def _visit_prep_session() -> Iterator[Session]:
    """Roll back fail-fast identity-lock contention before surfacing a 503."""

    with get_session() as session:
        try:
            yield session
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE_SQLSTATE:
                session.rollback()
            raise


@dataclass(frozen=True)
class _CredentialLocator:
    credential_id: TypeIDType
    visit_prep_id: TypeIDType
    owner_practice_member_id: TypeIDType
    owner_doctor_id: TypeIDType
    practice_id: TypeIDType
    delivery_link_id: TypeIDType | None = None


@dataclass(frozen=True)
class _AuthorizedInterview:
    visit_prep: VisitPrep
    schedule: VisitPrepScheduleGeneration
    credential: VisitPrepCredentialGeneration
    event_at: datetime
    continued_role: Literal["patient", "caregiver"] | None = None


def _canonical_token_hash(access_token: str | ConfirmedVisitPrepAccess) -> str:
    if isinstance(access_token, ConfirmedVisitPrepAccess):
        return access_token.token_hash
    try:
        return access_token_hash(access_token)
    except VisitPrepCredentialValidation:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from None


def _credential_locator(session: Session, *, token_hash: str) -> _CredentialLocator:
    alias = None
    credential = session.exec(
        select(VisitPrepCredentialGeneration).where(
            VisitPrepCredentialGeneration.token_hash == token_hash
        )
    ).one_or_none()
    if credential is None:
        alias = session.exec(
            select(VisitPrepDeliveryLink).where(
                VisitPrepDeliveryLink.token_hash == token_hash
            )
        ).one_or_none()
        if alias is not None:
            credential = session.get(VisitPrepCredentialGeneration, alias.credential_id)
        if credential is None:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    visit_prep = session.exec(
        select(VisitPrep).where(
            VisitPrep.id == credential.visit_prep_id,
            VisitPrep.owner_practice_member_id == credential.owner_practice_member_id,
        )
    ).one_or_none()
    if visit_prep is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    return _CredentialLocator(
        credential_id=credential.id,
        visit_prep_id=credential.visit_prep_id,
        owner_practice_member_id=credential.owner_practice_member_id,
        owner_doctor_id=visit_prep.owner_doctor_id,
        practice_id=visit_prep.practice_id,
        delivery_link_id=alias.id if alias else None,
    )


def confirmation_matches_invitation(
    *, access: ConfirmedVisitPrepAccess, presented_token_hash: str
) -> bool:
    """Identity-only routing hint; this is never a live authorization decision.

    A cookie for another preparation must not select its saved content when a
    new invitation is opened. Same-preparation lineage is checked under locks
    later; this read only preserves that new-invitation confirmation screen.
    """
    with _visit_prep_session() as session:
        try:
            proof = _credential_locator(session, token_hash=access.token_hash)
            presented = _credential_locator(session, token_hash=presented_token_hash)
        except VisitPrepInterviewUnavailable:
            return False
        return (
            proof.visit_prep_id == presented.visit_prep_id
            and proof.owner_practice_member_id == presented.owner_practice_member_id
        )


def _lock_authorized_interview(
    session: Session,
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    allow_wrong_recipient_replay: bool = False,
) -> _AuthorizedInterview:
    """Resolve and recheck a bearer using the canonical public lock order."""

    if not public_interview_api_enabled():
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    token_hash = _canonical_token_hash(access_token)
    locator = _credential_locator(session, token_hash=token_hash)

    # Practice -> Organization -> Doctor -> PracticeMember matches physician commands
    # and organization offboarding. The locator reads above are intentionally
    # unlocked and are revalidated under these locks.
    practice = session.exec(
        select(Practice)
        .where(Practice.id == locator.practice_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if practice is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    if practice.organization_id is not None:
        organization = session.exec(
            select(ProviderOrganization)
            .where(ProviderOrganization.id == practice.organization_id)
            .with_for_update(nowait=True)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if organization is None or organization.status != "active":
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    doctor = session.exec(
        select(Doctor)
        .where(Doctor.id == locator.owner_doctor_id)
        .with_for_update(nowait=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if doctor is None or doctor_tombstone(doctor) is not None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    owner = session.exec(
        select(PracticeMember)
        .where(
            PracticeMember.id == locator.owner_practice_member_id,
            PracticeMember.practice_id == practice.id,
            PracticeMember.doctor_id == doctor.id,
        )
        .with_for_update(nowait=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if owner is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    if owner.organization_roster_approval_id is not None:
        session.exec(
            select(OrganizationRosterApproval)
            .where(
                OrganizationRosterApproval.id == owner.organization_roster_approval_id
            )
            .with_for_update(nowait=True)
            .execution_options(populate_existing=True)
        ).one_or_none()

    visit_prep = session.exec(
        select(VisitPrep)
        .where(
            VisitPrep.id == locator.visit_prep_id,
            VisitPrep.owner_practice_member_id == owner.id,
            VisitPrep.practice_id == practice.id,
            VisitPrep.owner_doctor_id == owner.doctor_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if visit_prep is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    schedule = session.exec(
        select(VisitPrepScheduleGeneration)
        .where(
            VisitPrepScheduleGeneration.visit_prep_id == visit_prep.id,
            VisitPrepScheduleGeneration.owner_practice_member_id == owner.id,
        )
        .order_by(cast(Any, VisitPrepScheduleGeneration.generation).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    credential = session.exec(
        select(VisitPrepCredentialGeneration)
        .where(
            VisitPrepCredentialGeneration.visit_prep_id == visit_prep.id,
            VisitPrepCredentialGeneration.owner_practice_member_id == owner.id,
        )
        .order_by(cast(Any, VisitPrepCredentialGeneration.generation).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if schedule is None or credential is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    event_at = cast(datetime, session.exec(select(func.clock_timestamp())).one())
    if event_at.tzinfo is None or event_at.utcoffset() is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    event_at = event_at.astimezone(UTC)

    continued = isinstance(access_token, ConfirmedVisitPrepAccess)
    aliased = locator.delivery_link_id is not None and not continued
    delivery_deadline = None
    if aliased:
        from app.services.visit_prep_delivery_links import (
            DeliveryLinkUnavailable,
            verify_delivery_link,
        )

        alias = session.get(
            VisitPrepDeliveryLink, locator.delivery_link_id, populate_existing=True
        )
        try:
            if alias is None:
                raise DeliveryLinkUnavailable()
            verify_delivery_link(session, alias)
            delivery_deadline = alias.expires_at
            if (
                alias.token_hash != token_hash
                or alias.credential_id != credential.id
                or alias.visit_prep_id != visit_prep.id
                or alias.owner_practice_member_id != owner.id
                or alias.question_set_version_id != visit_prep.question_set_version_id
                or not alias.issued_at <= event_at < alias.expires_at
            ):
                raise DeliveryLinkUnavailable()
        except DeliveryLinkUnavailable:
            raise VisitPrepInterviewUnavailable(
                "Visit Prep link is unavailable"
            ) from None
    own_wrong_recipient_report = (
        allow_wrong_recipient_replay
        and visit_prep.revoked_at is not None
        and credential.revoked_at is not None
        and visit_prep.revocation_reason
        == credential.revocation_reason
        == "wrong_recipient"
        and visit_prep.revoked_by_ref
        == credential.revoked_by_ref
        == _wrong_recipient_actor(
            credential.token_hash if continued or aliased else token_hash
        )
    )
    if continued:
        delivery_deadline = validate_reschedule_continuity(
            session,
            prep=visit_prep,
            current=credential,
            access=access_token,
            at=event_at,
            own_wrong_recipient_replay=own_wrong_recipient_report,
        )
    # Sealed evidence and continuity reads may wait; apply deadlines to a fresh
    # clock after those reads, not the earlier locator/lock clock.
    event_at = cast(datetime, session.exec(select(func.clock_timestamp())).one())
    if event_at.tzinfo is None or event_at.utcoffset() is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    event_at = event_at.astimezone(UTC)
    decision = visit_prep_access.resolve_visit_prep_access(
        doctor, settings=visit_prep_access.VISIT_PREP_SETTINGS, at=event_at
    )
    if (
        not decision.eligible
        or decision.doctor_id != visit_prep.owner_doctor_id
        or decision.practice_id != visit_prep.practice_id
        or decision.practice_member_id != visit_prep.owner_practice_member_id
        or owner.access_revoked_at is not None
        or member_access_state(owner, at=event_at) not in SEAT_HOLDING_ACCESS_STATES
        or member_authorization_basis(owner, at=event_at, practice=practice)
        not in {"organization_roster", "workforce_attestation"}
        or not practice.baa_in_force(at=event_at)
        or not practice.entitled(at=event_at)
        or (visit_prep.revoked_at is not None and not own_wrong_recipient_report)
        or (
            visit_prep.lifecycle_state == "misdirected"
            and not own_wrong_recipient_report
        )
        or (credential.id != locator.credential_id and not continued)
        or credential.schedule_generation_id != schedule.id
        or (credential.revoked_at is not None and not own_wrong_recipient_report)
        or credential.token_format_version != 1
        or credential.issued_at > event_at
        or credential.expires_at <= event_at
        or (delivery_deadline is not None and delivery_deadline <= event_at)
        or (
            not hmac.compare_digest(credential.token_hash, token_hash)
            and not continued
            and not aliased
        )
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    continued_role = None
    if isinstance(access_token, ConfirmedVisitPrepAccess):
        if (
            visit_prep.confirmed_at != access_token.confirmed_at
            or visit_prep.confirmed_by_role != access_token.actor_role
            or (
                visit_prep.lifecycle_state
                not in {"confirmed", "in_progress", "completed"}
                and not own_wrong_recipient_report
            )
            or access_token.confirmed_at > event_at
            or access_token.expires_at <= event_at
        ):
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        continued_role = access_token.actor_role

    return _AuthorizedInterview(
        visit_prep=visit_prep,
        schedule=schedule,
        credential=credential,
        event_at=event_at,
        continued_role=continued_role,
    )


def _questions(
    session: Session,
    *,
    visit_prep: VisitPrep,
) -> list[VisitPrepQuestion]:
    version = session.exec(
        select(VisitPrepQuestionSetVersion).where(
            VisitPrepQuestionSetVersion.id == visit_prep.question_set_version_id,
            VisitPrepQuestionSetVersion.owner_practice_member_id
            == visit_prep.owner_practice_member_id,
            cast(Any, VisitPrepQuestionSetVersion.published_at).is_not(None),
        )
    ).one_or_none()
    if version is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    questions = list(
        session.exec(
            select(VisitPrepQuestion)
            .where(VisitPrepQuestion.question_set_version_id == version.id)
            .order_by(cast(Any, VisitPrepQuestion.position))
        ).all()
    )
    if len(questions) != 5 or [question.position for question in questions] != list(
        range(1, 6)
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    try:
        _definition_from_rows(version, questions, for_publish=True)
    except VisitPrepQuestionSetValidation:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from None
    return questions


def _responses(
    session: Session,
    *,
    visit_prep: VisitPrep,
) -> list[VisitPrepQuestionResponse]:
    responses = list(
        session.exec(
            select(VisitPrepQuestionResponse)
            .where(VisitPrepQuestionResponse.visit_prep_id == visit_prep.id)
            .order_by(cast(Any, VisitPrepQuestionResponse.submission_sequence))
        ).all()
    )
    if len(responses) > 5 or [row.submission_sequence for row in responses] != list(
        range(1, len(responses) + 1)
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    return responses


def _decrypted_answer(
    response: VisitPrepQuestionResponse | VisitPrepContributionEvent,
    *,
    question: VisitPrepQuestion,
) -> str:
    if (
        response.response_kind != "answered"
        or response.answer_format_version != 1
        or response.answer_ciphertext is None
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    try:
        payload = json.loads(decrypt_value(response.answer_ciphertext))
    except (DecryptionError, json.JSONDecodeError, TypeError, UnicodeError):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"kind", "value", "version"}
        or payload.get("kind") != question.kind
        or type(payload.get("version")) is not int
        or payload.get("version") != 1
        or not isinstance(payload.get("value"), str)
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    return cast(str, payload["value"])


def _question_is_visible(
    question: VisitPrepQuestion,
    *,
    questions_by_id: dict[TypeIDType, VisitPrepQuestion],
    responses_by_question: dict[TypeIDType, VisitPrepQuestionResponse],
) -> bool:
    if question.show_if_question_id is None:
        return True
    parent = responses_by_question.get(question.show_if_question_id)
    if parent is None or parent.response_kind == "skipped":
        return False
    parent_question = questions_by_id.get(question.show_if_question_id)
    if parent_question is None or question.show_if_values is None:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    return _decrypted_answer(parent, question=parent_question) in set(
        question.show_if_values
    )


def _validate_stored_response(
    response: VisitPrepQuestionResponse,
    *,
    question: VisitPrepQuestion,
    visit_prep: VisitPrep,
) -> None:
    if question.kind not in {"story", "list", "choice"}:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    if response.response_kind == "answered":
        answer = _decrypted_answer(response, question=question)
        try:
            normalized_answer = _normalized_answer(answer)
        except VisitPrepInterviewValidation:
            raise VisitPrepInterviewUnavailable(
                "Visit Prep link is unavailable"
            ) from None
        if normalized_answer != answer:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        if question.kind == "choice":
            allowed_values = {
                (
                    choice.value
                    if isinstance(choice, VisitPrepQuestionChoice)
                    else VisitPrepQuestionChoice.model_validate(choice).value
                )
                for choice in question.choice_options
            }
            if answer not in allowed_values:
                raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    elif (
        response.response_kind == "skipped"
        and response.answer_ciphertext is None
        and response.answer_format_version is None
    ):
        answer = None
    else:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    canonical = _CanonicalResponse(
        question_key=question.question_key,
        response_kind=cast(ResponseKind, response.response_kind),
        answer=answer,
    )
    expected_hmac = _response_payload_hmac(
        canonical,
        visit_prep_id=visit_prep.id,
    )
    if not hmac.compare_digest(response.payload_hmac_sha256, expected_hmac):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")


def _public_question(question: VisitPrepQuestion) -> InterviewQuestion:
    if question.kind not in {"story", "list", "choice"}:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    choices: list[InterviewChoice] = []
    for raw_choice in question.choice_options:
        choice = (
            raw_choice
            if isinstance(raw_choice, VisitPrepQuestionChoice)
            else VisitPrepQuestionChoice.model_validate(raw_choice)
        )
        choices.append(
            InterviewChoice(
                value=choice.value,
                label=choice.label,
                hint=choice.hint,
            )
        )
    return InterviewQuestion(
        key=question.question_key,
        position=question.position,
        prompt=question.prompt_text,
        helper_text=question.helper_text,
        kind=cast(QuestionKind, question.kind),
        choices=tuple(choices),
    )


def _interview_state(
    session: Session,
    *,
    authorized: _AuthorizedInterview,
) -> InterviewState:
    return replace(
        _unmarked_interview_state(session, authorized=authorized),
        continued_role=authorized.continued_role,
    )


def _unmarked_interview_state(
    session: Session,
    *,
    authorized: _AuthorizedInterview,
) -> InterviewState:
    visit_prep = authorized.visit_prep
    if visit_prep.lifecycle_state == "invited":
        return InterviewState(
            status="confirmation_required",
            physician_display_name=None,
            practice_display_name=None,
            scheduled_at=None,
            timezone_name=None,
            submitted_questions=0,
            maximum_questions=5,
            current_question=None,
        )

    questions = _questions(session, visit_prep=visit_prep)
    responses = _responses(session, visit_prep=visit_prep)
    if visit_prep.lifecycle_state not in {"confirmed", "in_progress", "completed"}:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    questions_by_id = {question.id: question for question in questions}
    responses_by_question = {response.question_id: response for response in responses}
    if len(responses_by_question) != len(responses):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    current_question: VisitPrepQuestion | None = None
    visible_response_count = 0
    for question in questions:
        visible = _question_is_visible(
            question,
            questions_by_id=questions_by_id,
            responses_by_question=responses_by_question,
        )
        response = responses_by_question.get(question.id)
        if not visible:
            if response is not None:
                raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
            continue
        if response is None:
            if current_question is None:
                current_question = question
            continue
        if current_question is not None:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        if response.submission_sequence != visible_response_count + 1:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        _validate_stored_response(
            response,
            question=question,
            visit_prep=visit_prep,
        )
        visible_response_count += 1

    if visible_response_count != len(responses):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    if visit_prep.lifecycle_state == "completed":
        if current_question is not None:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        return InterviewState(
            status="completed",
            physician_display_name=visit_prep.physician_display_name,
            practice_display_name=visit_prep.practice_display_name,
            scheduled_at=authorized.schedule.scheduled_at,
            timezone_name=authorized.schedule.timezone_name,
            submitted_questions=visible_response_count,
            maximum_questions=5,
            current_question=None,
        )

    if visit_prep.lifecycle_state == "confirmed" and responses:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    if visit_prep.lifecycle_state == "in_progress" and not responses:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

    return InterviewState(
        status=("in_progress" if current_question is not None else "ready_to_complete"),
        physician_display_name=visit_prep.physician_display_name,
        practice_display_name=visit_prep.practice_display_name,
        scheduled_at=authorized.schedule.scheduled_at,
        timezone_name=authorized.schedule.timezone_name,
        submitted_questions=visible_response_count,
        maximum_questions=5,
        current_question=(
            _public_question(current_question) if current_question is not None else None
        ),
    )


def _recipient_identity(visit_prep: VisitPrep) -> tuple[str, str]:
    if visit_prep.recipient_snapshot_format_version != 1:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    try:
        payload = json.loads(decrypt_value(visit_prep.recipient_snapshot_ciphertext))
    except (DecryptionError, json.JSONDecodeError, TypeError, UnicodeError):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from None
    if (
        not isinstance(payload, dict)
        or type(payload.get("version")) is not int
        or payload.get("version") != 1
        or payload.get("role") not in {"patient", "caregiver"}
        or not isinstance(payload.get("first_name"), str)
    ):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    try:
        first_name = _normalized_confirmation_name(payload["first_name"])
    except VisitPrepInterviewValidation:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from None
    return first_name, cast(str, payload["role"])


def _canonical_response(
    value: ResponseInput,
    *,
    question: VisitPrepQuestion,
) -> _CanonicalResponse:
    if question.kind not in {"story", "list", "choice"}:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    if value.response_kind == "answered":
        assert value.answer is not None
        if question.kind == "choice":
            allowed_values = {
                (
                    choice.value
                    if isinstance(choice, VisitPrepQuestionChoice)
                    else VisitPrepQuestionChoice.model_validate(choice).value
                )
                for choice in question.choice_options
            }
            if value.answer not in allowed_values:
                raise VisitPrepInterviewValidation("answer is invalid")
            answer = value.answer
        else:
            answer = _normalized_answer(value.answer)
    else:
        answer = None
    return _CanonicalResponse(
        question_key=value.question_key,
        response_kind=value.response_kind,
        answer=answer,
    )


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise VisitPrepInterviewConflict("Visit Prep state changed") from None


def _resume_interview(
    *, access_token: str | ConfirmedVisitPrepAccess
) -> tuple[InterviewState, ConfirmedVisitPrepAccess | None]:
    """Resume one currently authorized Visit Prep without returning prior answers."""

    with _visit_prep_session() as session:
        authorized = _lock_authorized_interview(session, access_token=access_token)
        state = _interview_state(session, authorized=authorized)
        try:
            stage_first_flow_open(
                session,
                prep=authorized.visit_prep,
                credential=authorized.credential,
                at=authorized.event_at,
            )
        except InvitationOpenEvidenceInvalid:
            raise VisitPrepInterviewUnavailable(
                "Visit Prep link is unavailable"
            ) from None
        # Audit insertion may wait. Recheck current database time and authority
        # before committing an observation or disclosing the resolved snapshot.
        current = _lock_authorized_interview(session, access_token=access_token)
        continuation = None
        if isinstance(access_token, ConfirmedVisitPrepAccess):
            continuation = ConfirmedVisitPrepAccess(
                token_hash=current.credential.token_hash,
                actor_role=access_token.actor_role,
                confirmed_at=access_token.confirmed_at,
                expires_at=current.credential.expires_at,
            )
        _commit(session)
        return replace(state, continued_role=current.continued_role), continuation


def get_interview(*, access_token: str | ConfirmedVisitPrepAccess) -> InterviewState:
    return _resume_interview(access_token=access_token)[0]


def resume_confirmed_interview(
    *, access: ConfirmedVisitPrepAccess
) -> tuple[InterviewState, ConfirmedVisitPrepAccess]:
    state, continuation = _resume_interview(access_token=access)
    assert continuation is not None
    return state, continuation


def _wrong_recipient_actor(token_hash: str) -> str:
    # The verifier is already a context-separated HMAC, never the bearer.
    # Attribute the report to a credential holder, not a verified patient.
    return f"visit-prep-recipient-report:{token_hash}"


def report_wrong_recipient(*, access_token: str | ConfirmedVisitPrepAccess) -> None:
    """Stop an invitation; quarantine interacted content without deleting it.

    A holder may report misdelivery without passing intended-name confirmation.
    No contact data, answers, clinical identifiers or incident reference returns
    to the public caller. Only the same current credential may replay its own
    report; this exception never permits interview reads or writes.
    """

    with _visit_prep_session() as session:
        authorized = _lock_authorized_interview(
            session, access_token=access_token, allow_wrong_recipient_replay=True
        )
        visit_prep = authorized.visit_prep
        credential = authorized.credential
        if visit_prep.revoked_at is not None:
            _commit(session)
            return
        actor_ref = _wrong_recipient_actor(credential.token_hash)
        visit_prep.revoked_at = credential.revoked_at = authorized.event_at
        visit_prep.revoked_by_ref = credential.revoked_by_ref = actor_ref
        visit_prep.revocation_reason = credential.revocation_reason = "wrong_recipient"
        visit_prep.updated_at = authorized.event_at
        if visit_prep.confirmed_at is not None:
            visit_prep.lifecycle_state = "misdirected"
            visit_prep.misdirected_at = authorized.event_at
            visit_prep.misdirected_reason = "wrong_recipient"
            visit_prep.misdirected_incident_ref = f"visit-prep-incident:{uuid4()}"
            visit_prep.misdirected_disposition = "pending_review"
        session.add(credential)
        session.add(visit_prep)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise VisitPrepInterviewConflict("Visit Prep state changed") from None
        _commit(session)


def confirm_interview(
    *,
    access_token: str,
    first_name: str,
    role: Literal["patient", "caregiver"],
) -> InterviewMutationResult:
    """Confirm the intended recipient and snapshot clinical.v1 retention."""

    normalized_name = _normalized_confirmation_name(first_name)
    if not isinstance(role, str) or role not in {"patient", "caregiver"}:
        raise VisitPrepInterviewValidation("confirmation is invalid")

    with _visit_prep_session() as session:
        authorized = _lock_authorized_interview(session, access_token=access_token)
        visit_prep = authorized.visit_prep
        expected_name, expected_role = _recipient_identity(visit_prep)
        if not hmac.compare_digest(
            normalized_name.encode("utf-8"), expected_name.encode("utf-8")
        ) or not hmac.compare_digest(role, expected_role):
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

        if visit_prep.lifecycle_state == "invited":
            visit_prep.lifecycle_state = "confirmed"
            visit_prep.confirmed_at = authorized.event_at
            visit_prep.confirmed_by_role = role
            visit_prep.retention_policy_version = CLINICAL_RETENTION_POLICY_VERSION
            visit_prep.retain_until = _calendar_years_after(
                authorized.schedule.scheduled_at,
                CLINICAL_RETENTION_YEARS,
            )
            visit_prep.updated_at = authorized.event_at
            session.add(visit_prep)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                raise VisitPrepInterviewConflict("Visit Prep state changed") from None
        elif visit_prep.lifecycle_state not in {
            "confirmed",
            "in_progress",
            "completed",
        }:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        elif (
            visit_prep.confirmed_by_role != role
            or visit_prep.retention_policy_version != CLINICAL_RETENTION_POLICY_VERSION
            or visit_prep.retain_until is None
        ):
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

        state = _interview_state(session, authorized=authorized)
        if visit_prep.confirmed_at is None:
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
        continuation = ConfirmedVisitPrepAccess(
            token_hash=authorized.credential.token_hash,
            actor_role=role,
            confirmed_at=visit_prep.confirmed_at,
            expires_at=authorized.credential.expires_at,
        )
        _lock_authorized_interview(session, access_token=access_token)
        _commit(session)
        return InterviewMutationResult(
            interview=replace(state, continued_role=role),
            disposition="confirmed",
            continuation=continuation,
        )


def submit_response(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    idempotency_key: str,
    response: ResponseInput,
) -> InterviewMutationResult:
    """Append one answer or explicit skip with replay-safe semantics."""

    canonical_key = _canonical_idempotency_key(idempotency_key)
    submitted_response = _validated_response_input(response)
    with _visit_prep_session() as session:
        authorized = _lock_authorized_interview(session, access_token=access_token)
        visit_prep = authorized.visit_prep
        questions = _questions(session, visit_prep=visit_prep)
        question = next(
            (
                candidate
                for candidate in questions
                if candidate.question_key == submitted_response.question_key
            ),
            None,
        )
        if question is None:
            raise VisitPrepInterviewConflict("question is not current")
        canonical_response = _canonical_response(
            submitted_response,
            question=question,
        )
        payload_hmac = _response_payload_hmac(
            canonical_response,
            visit_prep_id=visit_prep.id,
        )

        # This lookup deliberately precedes current-question and lifecycle
        # validation. Exact retries remain successful after progress,
        # completion, or a credential rotation; divergent reuse is fixed 409.
        existing = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == visit_prep.id,
                VisitPrepQuestionResponse.idempotency_key == canonical_key,
            )
        ).one_or_none()
        if existing is not None:
            if not hmac.compare_digest(existing.payload_hmac_sha256, payload_hmac):
                raise VisitPrepInterviewConflict(
                    "idempotency key belongs to a different response"
                )
            state = _interview_state(session, authorized=authorized)
            _commit(session)
            return InterviewMutationResult(interview=state, disposition="replayed")

        state = _interview_state(session, authorized=authorized)
        if state.status not in {"in_progress"} or state.current_question is None:
            raise VisitPrepInterviewConflict("Visit Prep cannot accept a response")
        if state.current_question.key != canonical_response.question_key:
            raise VisitPrepInterviewConflict("question is not current")

        if canonical_response.response_kind == "answered":
            assert canonical_response.answer is not None
            try:
                answer_ciphertext = encrypt_value(
                    _answer_envelope(
                        kind=question.kind,
                        value=canonical_response.answer,
                    )
                )
            except EncryptionError:
                raise VisitPrepInterviewUnavailable(
                    "Visit Prep link is unavailable"
                ) from None
            answer_format_version: int | None = 1
        else:
            answer_ciphertext = None
            answer_format_version = None

        existing_responses = _responses(session, visit_prep=visit_prep)
        if visit_prep.lifecycle_state == "confirmed":
            visit_prep.lifecycle_state = "in_progress"
            visit_prep.started_at = authorized.event_at
            visit_prep.updated_at = authorized.event_at
            session.add(visit_prep)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                raise VisitPrepInterviewConflict("Visit Prep state changed") from None

        response_row = VisitPrepQuestionResponse(
            visit_prep_id=visit_prep.id,
            owner_practice_member_id=visit_prep.owner_practice_member_id,
            question_set_version_id=visit_prep.question_set_version_id,
            question_id=question.id,
            credential_generation=authorized.credential.generation,
            submission_sequence=len(existing_responses) + 1,
            response_kind=canonical_response.response_kind,
            answer_ciphertext=answer_ciphertext,
            answer_format_version=answer_format_version,
            idempotency_key=canonical_key,
            payload_hmac_sha256=payload_hmac,
        )
        session.add(response_row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise VisitPrepInterviewConflict("Visit Prep state changed") from None
        state = _interview_state(session, authorized=authorized)
        _commit(session)
        return InterviewMutationResult(interview=state, disposition="accepted")


def complete_interview(
    *, access_token: str | ConfirmedVisitPrepAccess
) -> InterviewMutationResult:
    """Explicitly complete an interview only after all applicable questions."""

    # Late import keeps the shared contribution/owner projection acyclic.
    from app.services.visit_prep_patient_handoff import stage_patient_handoff

    with _visit_prep_session() as session:
        authorized = _lock_authorized_interview(session, access_token=access_token)
        visit_prep = authorized.visit_prep
        if visit_prep.lifecycle_state == "completed":
            state = _interview_state(session, authorized=authorized)
            stage_patient_handoff(session, access_token=access_token)
            _commit(session)
            return InterviewMutationResult(interview=state, disposition="completed")

        state = _interview_state(session, authorized=authorized)
        if state.status != "ready_to_complete":
            raise VisitPrepInterviewConflict("Visit Prep is incomplete")
        visit_prep.lifecycle_state = "completed"
        visit_prep.completed_at = authorized.event_at
        visit_prep.updated_at = authorized.event_at
        session.add(visit_prep)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise VisitPrepInterviewConflict("Visit Prep state changed") from None
        state = _interview_state(session, authorized=authorized)
        stage_patient_handoff(session, access_token=access_token)
        _commit(session)
        return InterviewMutationResult(interview=state, disposition="completed")
