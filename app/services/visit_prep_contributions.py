"""One current contribution projection shared by patient and provider workflows.

This loader is internal: the caller must lock and authorize the preparation in
its transaction before calling it. It does not authorize a bearer read, expose
an API, persist clinical content, generate a synthesis or call a model.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import replace
from typing import Any, cast

from app.services.visit_prep_contribution_contract import (
    ContributionBasis,
    ContributionEventKind,
    ContributionMutationReceipt,
    ContributionRole,
    ContributionTopic,
    PatientContribution,
    contribution_payload_hmac,
)
from app.services.visit_prep_interview_contract import (
    ResponseInput,
    ResponseKind,
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
    _answer_envelope,
    _canonical_idempotency_key,
    _CanonicalResponse,
    _normalized_answer,
    _normalized_confirmation_name,
    _validated_response_input,
)
from app.services.visit_prep_interviews import (
    _AuthorizedInterview,
    _canonical_response,
    _commit,
    _decrypted_answer,
    _lock_authorized_interview,
    _public_question,
    _question_is_visible,
    _questions,
    _recipient_identity,
    _responses,
    _validate_stored_response,
    _visit_prep_session,
)
from app.services.visit_prep_patient_continuation import ConfirmedVisitPrepAccess
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
)

from activemodel.types import TypeIDType
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from app.models.visit_prep_question_set import VisitPrepQuestion
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select


def _unavailable() -> VisitPrepInterviewUnavailable:
    return VisitPrepInterviewUnavailable("Visit Prep link is unavailable")


def _explanation_words(event: VisitPrepContributionEvent) -> str | None:
    if (
        event.response_kind == "skipped"
        and event.answer_ciphertext is None
        and event.answer_format_version is None
    ):
        return None
    if (
        event.response_kind != "answered"
        or event.answer_format_version != 1
        or not event.answer_ciphertext
    ):
        raise _unavailable()
    try:
        envelope = json.loads(decrypt_value(event.answer_ciphertext))
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"version", "kind", "value"}
            or type(envelope["version"]) is not int
            or envelope["version"] != 1
            or envelope["kind"] != "story"
        ):
            raise _unavailable()
        words = _normalized_answer(envelope["value"])
        if words != envelope["value"]:
            raise _unavailable()
        return words
    except (DecryptionError, ValueError, TypeError, VisitPrepInterviewValidation):
        raise _unavailable() from None


def _applicable_topics(
    questions: list[VisitPrepQuestion], topics: dict[TypeIDType, ContributionTopic]
) -> None:
    """Recompute dependencies in their physician-owned published order."""

    for question in questions:
        visible = True
        if question.show_if_question_id is not None:
            parent = topics.get(question.show_if_question_id)
            if parent is None or question.show_if_values is None:
                raise _unavailable()
            visible = (
                parent.applicable
                and parent.response_kind == "answered"
                and parent.answer in question.show_if_values
            )
        topics[question.id] = replace(topics[question.id], applicable=visible)


def _load_contribution(
    session: Session, *, visit_prep: VisitPrep
) -> PatientContribution:
    """Validate immutable sources, apply corrections and recompute applicability.

    Completion of the original interview is historical evidence, not proof that
    the revised contribution is complete or reviewed. Hidden answers remain in
    history but cannot count as active answers in a downstream package.
    """

    if (
        visit_prep.lifecycle_state not in {"confirmed", "in_progress", "completed"}
        or visit_prep.confirmed_by_role not in {"patient", "caregiver"}
        or visit_prep.confirmed_at is None
        or visit_prep.revoked_at is not None
    ):
        raise _unavailable()
    role = cast(ContributionRole, visit_prep.confirmed_by_role)
    questions = _questions(session, visit_prep=visit_prep)
    responses = _responses(session, visit_prep=visit_prep)
    questions_by_id = {question.id: question for question in questions}
    by_question = {response.question_id: response for response in responses}
    by_response = {response.id: response for response in responses}
    if len(by_question) != len(responses):
        raise _unavailable()

    topics: dict[TypeIDType, ContributionTopic] = {}
    first_missing = False
    accepted_count = 0
    for question in questions:
        visible = _question_is_visible(
            question,
            questions_by_id=questions_by_id,
            responses_by_question=by_question,
        )
        response = by_question.get(question.id)
        if response is not None:
            if (
                not visible
                or first_missing
                or response.submission_sequence != accepted_count + 1
                or response.owner_practice_member_id
                != visit_prep.owner_practice_member_id
                or response.question_set_version_id
                != visit_prep.question_set_version_id
                or response.recorded_at is None
            ):
                raise _unavailable()
            _validate_stored_response(
                response, question=question, visit_prep=visit_prep
            )
            answer = (
                _decrypted_answer(response, question=question)
                if response.response_kind == "answered"
                else None
            )
            accepted_count += 1
        else:
            if visible:
                first_missing = True
            answer = None
        topics[question.id] = ContributionTopic(
            question=_public_question(question),
            applicable=visible,
            response_kind=cast(ResponseKind, response.response_kind)
            if response
            else None,
            answer=answer,
            original_answer=answer,
            original_response_kind=cast(ResponseKind, response.response_kind)
            if response
            else None,
            original_response_id=response.id if response else None,
            original_recorded_at=response.recorded_at if response else None,
            current_correction_id=None,
            recorded_at=response.recorded_at if response else None,
        )
    if accepted_count != len(responses) or (
        visit_prep.lifecycle_state == "completed" and first_missing
    ):
        raise _unavailable()

    events = session.exec(
        select(VisitPrepContributionEvent)
        .where(VisitPrepContributionEvent.visit_prep_id == visit_prep.id)
        .order_by(cast(Any, VisitPrepContributionEvent.event_sequence))
    ).all()
    last_response_count = 0
    current_review: VisitPrepContributionEvent | None = None
    for sequence, event in enumerate(events, 1):
        if (
            event.event_sequence != sequence
            or event.basis_event_sequence != sequence - 1
            or not max(1, last_response_count)
            <= event.basis_response_count
            <= len(responses)
            or event.actor_role != role
            or event.owner_practice_member_id != visit_prep.owner_practice_member_id
            or event.question_set_version_id != visit_prep.question_set_version_id
            or event.recorded_at is None
            or event.recorded_at.tzinfo is None
        ):
            raise _unavailable()
        # Roots serialize original and revision writes. The DB-owned times
        # prove which original rows existed at this event, not just today's count.
        recorded_response_count = sum(
            row.recorded_at is not None and row.recorded_at <= event.recorded_at
            for row in responses
        )
        if event.basis_response_count != recorded_response_count:
            raise _unavailable()
        basis = ContributionBasis(
            event.basis_response_count, event.basis_event_sequence
        )
        _applicable_topics(questions, topics)
        if event.event_kind == "explanation":
            if event.question_id is None:
                raise _unavailable()
            question = questions_by_id.get(event.question_id)
            topic = topics.get(event.question_id)
            if (
                question is None
                or topic is None
                or question.kind != "choice"
                or not topic.applicable
                or topic.response_kind != "answered"
                or event.response_id != topic.original_response_id
                or event.target_revision_id != topic.current_correction_id
            ):
                raise _unavailable()
            words = _explanation_words(event)
            fingerprint = contribution_payload_hmac(
                visit_prep_id=visit_prep.id,
                actor_role=role,
                event_kind="explanation",
                basis=basis,
                original_response_id=topic.original_response_id,
                question_id=question.id,
                target_revision_id=topic.current_correction_id,
                response=_CanonicalResponse(
                    question.question_key,
                    cast(ResponseKind, event.response_kind),
                    words,
                ),
            )
            topics[question.id] = replace(
                topic,
                explanation_id=event.id,
                explanation_words=words,
                explanation_recorded_at=event.recorded_at,
            )
            current_review = None
        elif event.event_kind in {"correction", "topic_answer"}:
            if event.target_revision_id is not None:
                raise _unavailable()
            original = None
            if event.event_kind == "correction":
                if event.response_id is None or event.question_id is not None:
                    raise _unavailable()
                original = by_response.get(event.response_id)
                if original is None:
                    raise _unavailable()
                question = questions_by_id.get(original.question_id)
            else:
                if event.response_id is not None or event.question_id is None:
                    raise _unavailable()
                question = questions_by_id.get(event.question_id)
                if (
                    question is None
                    or question.id in by_question
                    or question.show_if_question_id is None
                    or _question_is_visible(
                        question,
                        questions_by_id=questions_by_id,
                        responses_by_question=by_question,
                    )
                    or not topics[question.id].applicable
                    or topics[question.show_if_question_id].current_correction_id
                    is None
                ):
                    raise _unavailable()
            if question is None:
                raise _unavailable()
            if original is not None and (
                original.recorded_at is None or original.recorded_at > event.recorded_at
            ):
                raise _unavailable()
            if event.response_kind == "answered":
                answer = _decrypted_answer(event, question=question)
            elif (
                event.response_kind == "skipped"
                and event.answer_ciphertext is None
                and event.answer_format_version is None
            ):
                answer = None
            else:
                raise _unavailable()
            try:
                canonical = _canonical_response(
                    ResponseInput(
                        question.question_key,
                        cast(ResponseKind, event.response_kind),
                        answer,
                    ),
                    question=question,
                )
            except VisitPrepInterviewValidation:
                raise _unavailable() from None
            if canonical.answer != answer:
                raise _unavailable()
            fingerprint = contribution_payload_hmac(
                visit_prep_id=visit_prep.id,
                actor_role=role,
                event_kind=cast(ContributionEventKind, event.event_kind),
                basis=basis,
                original_response_id=original.id if original else None,
                question_id=question.id if original is None else None,
                response=canonical,
            )
            previous = topics[question.id]
            additional = {}
            if (
                event.event_kind == "topic_answer"
                and previous.additional_answer_source_id is None
            ):
                additional = dict(
                    additional_answer_source_id=event.id,
                    additional_answer=answer,
                    additional_response_kind=canonical.response_kind,
                    additional_recorded_at=event.recorded_at,
                )
            topics[question.id] = replace(
                previous,
                answer=answer,
                response_kind=canonical.response_kind,
                current_correction_id=event.id,
                recorded_at=event.recorded_at,
                explanation_id=None,
                explanation_words=None,
                explanation_recorded_at=None,
                **additional,
            )
            current_review = None
        elif event.event_kind == "review" and all(
            value is None
            for value in (
                event.response_id,
                event.question_id,
                event.response_kind,
                event.answer_ciphertext,
                event.answer_format_version,
                event.target_revision_id,
            )
        ):
            fingerprint = contribution_payload_hmac(
                visit_prep_id=visit_prep.id,
                actor_role=role,
                event_kind="review",
                basis=basis,
            )
            current_review = event
        else:
            raise _unavailable()
        if not hmac.compare_digest(event.payload_hmac_sha256, fingerprint):
            raise _unavailable()
        last_response_count = event.basis_response_count

    # Follow physician-owned conditional dependencies in their published order.
    # Applicability depends on the effective parent, never its original wording.
    _applicable_topics(questions, topics)
    complete = all(
        topic.response_kind is not None for topic in topics.values() if topic.applicable
    )
    reviewed = bool(
        complete
        and current_review is not None
        and current_review.basis_response_count == len(responses)
        and current_review.event_sequence == len(events)
    )
    return PatientContribution(
        basis=ContributionBasis(len(responses), len(events)),
        actor_role=role,
        topics=tuple(topics[question.id] for question in questions),
        original_interview_completed=visit_prep.lifecycle_state == "completed",
        current_answers_complete=complete,
        current_answers_reviewed=reviewed,
        current_review_id=current_review.id if reviewed and current_review else None,
        reviewed_at=current_review.recorded_at if reviewed and current_review else None,
    )


def _require_recipient(
    authorized: _AuthorizedInterview, *, first_name: str, role: ContributionRole
) -> None:
    """Recheck the same confirmation evidence; never claim verified identity."""

    if authorized.continued_role is not None:
        if role != authorized.continued_role:
            raise _unavailable()
        if first_name == "":
            return
    name = _normalized_confirmation_name(first_name)
    if not isinstance(role, str) or role not in {"patient", "caregiver"}:
        raise VisitPrepInterviewValidation("confirmation is invalid")
    prep = authorized.visit_prep
    expected_name, expected_role = _recipient_identity(prep)
    if (
        not hmac.compare_digest(name.encode("utf-8"), expected_name.encode("utf-8"))
        or not hmac.compare_digest(role, expected_role)
        or prep.confirmed_by_role != role
    ):
        raise _unavailable()


def _receipt(
    contribution: PatientContribution, *, replayed: bool = False
) -> ContributionMutationReceipt:
    return ContributionMutationReceipt(
        disposition="replayed" if replayed else "accepted",
        basis=contribution.basis,
        current_answers_complete=contribution.current_answers_complete,
        current_answers_reviewed=contribution.current_answers_reviewed,
    )


def _existing_event(
    session: Session, *, prep: VisitPrep, key: str, fingerprint: str
) -> bool:
    existing = session.exec(
        select(VisitPrepContributionEvent).where(
            VisitPrepContributionEvent.visit_prep_id == prep.id,
            VisitPrepContributionEvent.idempotency_key == key,
        )
    ).one_or_none()
    if existing is None:
        return False
    if not hmac.compare_digest(existing.payload_hmac_sha256, fingerprint):
        raise VisitPrepInterviewConflict("Visit Prep contribution state changed")
    return True


def _append_event_in_session(
    session: Session,
    *,
    authorized: _AuthorizedInterview,
    role: ContributionRole,
    basis: ContributionBasis,
    key: str,
    fingerprint: str,
    response_id: TypeIDType | None = None,
    question_id: TypeIDType | None = None,
    response_kind: ResponseKind | None = None,
    ciphertext: str | None = None,
    explanation: bool = False,
    target_revision_id: TypeIDType | None = None,
) -> PatientContribution:
    prep = authorized.visit_prep
    session.add(
        VisitPrepContributionEvent(
            visit_prep_id=prep.id,
            owner_practice_member_id=prep.owner_practice_member_id,
            question_set_version_id=prep.question_set_version_id,
            credential_generation=authorized.credential.generation,
            actor_role=role,
            event_kind=(
                "explanation"
                if explanation
                else "correction"
                if response_id is not None
                else "topic_answer"
                if question_id is not None
                else "review"
            ),
            event_sequence=basis.event_sequence + 1,
            basis_event_sequence=basis.event_sequence,
            basis_response_count=basis.original_response_count,
            response_id=response_id,
            target_revision_id=target_revision_id,
            question_id=question_id,
            response_kind=response_kind,
            answer_ciphertext=ciphertext,
            answer_format_version=1 if ciphertext is not None else None,
            idempotency_key=key,
            payload_hmac_sha256=fingerprint,
        )
    )
    try:
        session.flush()
    except IntegrityError:
        raise VisitPrepInterviewConflict(
            "Visit Prep contribution state changed"
        ) from None
    return _load_contribution(session, visit_prep=prep)


def explain_contribution(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: ContributionRole,
    expected_basis: ContributionBasis,
    question_key: str,
    patient_words: str | None,
    idempotency_key: str,
) -> ContributionMutationReceipt:
    """Save or withdraw explicit words alongside the exact current coded answer.

    A null value is an attributable withdrawal, not deletion. A later choice
    revision invalidates the explanation even if the selected code is unchanged.
    No AI, provider verification, consent or clinical-record write is implied.
    """
    with _visit_prep_session() as session:
        try:
            receipt = explain_contribution_in_session(
                session,
                access_token=access_token,
                first_name=first_name,
                role=role,
                expected_basis=expected_basis,
                question_key=question_key,
                patient_words=patient_words,
                idempotency_key=idempotency_key,
            )
            _commit(session)
            return receipt
        except BaseException:
            session.rollback()
            raise


def explain_contribution_in_session(
    session: Session,
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: ContributionRole,
    expected_basis: ContributionBasis,
    question_key: str,
    patient_words: str | None,
    idempotency_key: str,
) -> ContributionMutationReceipt:
    """Stage the same explicit explanation; caller owns commit and rollback.

    AI acceptance uses this to bind the explanation, decision and audit in one
    transaction. There is no model-selected code or alternate explanation store.
    """
    if type(expected_basis) is not ContributionBasis or type(question_key) is not str:
        raise VisitPrepInterviewValidation("contribution basis is invalid")
    words = _normalized_answer(patient_words) if patient_words is not None else None
    key = _canonical_idempotency_key(idempotency_key)
    authorized = _lock_authorized_interview(session, access_token=access_token)
    _require_recipient(authorized, first_name=first_name, role=role)
    prep = authorized.visit_prep
    current = _load_contribution(session, visit_prep=prep)
    topic = next((t for t in current.topics if t.question.key == question_key), None)
    if topic is None or topic.question.kind != "choice":
        raise VisitPrepInterviewValidation("explanation topic is invalid")
    question = next(
        q
        for q in _questions(session, visit_prep=prep)
        if q.question_key == question_key
    )
    # Replay binds the historical target, not today's possibly revised choice.
    existing = session.exec(
        select(VisitPrepContributionEvent).where(
            VisitPrepContributionEvent.visit_prep_id == prep.id,
            VisitPrepContributionEvent.idempotency_key == key,
        )
    ).one_or_none()
    target = existing.target_revision_id if existing else topic.current_correction_id
    original = existing.response_id if existing else topic.original_response_id
    fingerprint = contribution_payload_hmac(
        visit_prep_id=prep.id,
        actor_role=role,
        event_kind="explanation",
        basis=expected_basis,
        original_response_id=original,
        question_id=question.id,
        target_revision_id=target,
        response=_CanonicalResponse(
            question_key, "answered" if words is not None else "skipped", words
        ),
    )
    replayed = _existing_event(session, prep=prep, key=key, fingerprint=fingerprint)
    if not replayed:
        if (
            current.basis != expected_basis
            or not topic.applicable
            or topic.response_kind != "answered"
        ):
            raise VisitPrepInterviewConflict("Visit Prep contribution state changed")
        try:
            ciphertext = (
                encrypt_value(_answer_envelope(kind="story", value=words))
                if words is not None
                else None
            )
        except EncryptionError:
            raise _unavailable() from None
        current = _append_event_in_session(
            session,
            authorized=authorized,
            role=role,
            basis=expected_basis,
            key=key,
            fingerprint=fingerprint,
            response_id=original,
            question_id=question.id,
            explanation=True,
            target_revision_id=target,
            response_kind="answered" if words is not None else "skipped",
            ciphertext=ciphertext,
        )
    final = _lock_authorized_interview(session, access_token=access_token)
    _require_recipient(final, first_name=first_name, role=role)
    return _receipt(current, replayed=replayed)


def correct_contribution(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: ContributionRole,
    expected_basis: ContributionBasis,
    idempotency_key: str,
    response: ResponseInput,
) -> ContributionMutationReceipt:
    """Commit a patient-owned revision; exact replay returns no answer plaintext."""
    with _visit_prep_session() as session:
        try:
            receipt = correct_contribution_in_session(
                session,
                access_token=access_token,
                first_name=first_name,
                role=role,
                expected_basis=expected_basis,
                idempotency_key=idempotency_key,
                response=response,
            )
        except VisitPrepInterviewConflict:
            session.rollback()
            raise
        _commit(session)
        return receipt


def correct_contribution_in_session(
    session: Session,
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: ContributionRole,
    expected_basis: ContributionBasis,
    idempotency_key: str,
    response: ResponseInput,
) -> ContributionMutationReceipt:
    """Stage the existing patient correction in a caller-owned transaction.

    Reacquire ordinary public/recipient authority, preserve original sources and
    exact replay, and recheck time-dependent authority after the flush. Never
    open a session, commit or roll back. A receipt is staged, not a saved answer.
    The caller must atomically bind its own qualified action evidence, recheck
    all authority after later work, and acknowledge commit before disclosure.
    Any denial or uncertain commit must discard the receipt. This grants no AI,
    bot, model, provider, consent or identity capability.
    """
    if not isinstance(expected_basis, ContributionBasis):
        raise VisitPrepInterviewValidation("contribution basis is invalid")
    key = _canonical_idempotency_key(idempotency_key)
    submitted = _validated_response_input(response)
    authorized = _lock_authorized_interview(session, access_token=access_token)
    _require_recipient(authorized, first_name=first_name, role=role)
    prep = authorized.visit_prep
    current = _load_contribution(session, visit_prep=prep)
    topic = next(
        (
            item
            for item in current.topics
            if item.question.key == submitted.question_key
        ),
        None,
    )
    if topic is None:
        raise VisitPrepInterviewConflict("Visit Prep contribution state changed")
    questions = _questions(session, visit_prep=prep)
    question = next(
        row for row in questions if row.question_key == submitted.question_key
    )
    additional = topic.original_response_id is None
    if additional and (
        question.show_if_question_id is None
        or _question_is_visible(
            question,
            questions_by_id={row.id: row for row in questions},
            responses_by_question={
                row.question_id: row for row in _responses(session, visit_prep=prep)
            },
        )
    ):
        raise VisitPrepInterviewConflict("Visit Prep contribution state changed")
    canonical = _canonical_response(submitted, question=question)
    fingerprint = contribution_payload_hmac(
        visit_prep_id=prep.id,
        actor_role=role,
        event_kind="topic_answer" if additional else "correction",
        basis=expected_basis,
        original_response_id=topic.original_response_id,
        question_id=question.id if additional else None,
        response=canonical,
    )
    replayed = _existing_event(session, prep=prep, key=key, fingerprint=fingerprint)
    if not replayed:
        if current.basis != expected_basis or not topic.applicable:
            raise VisitPrepInterviewConflict("Visit Prep contribution state changed")
        try:
            ciphertext = (
                encrypt_value(
                    _answer_envelope(kind=question.kind, value=canonical.answer)
                )
                if canonical.answer is not None
                else None
            )
        except EncryptionError:
            raise _unavailable() from None
        current = _append_event_in_session(
            session,
            authorized=authorized,
            role=role,
            basis=expected_basis,
            key=key,
            fingerprint=fingerprint,
            response_id=topic.original_response_id,
            question_id=question.id if additional else None,
            response_kind=canonical.response_kind,
            ciphertext=ciphertext,
        )
    final = _lock_authorized_interview(session, access_token=access_token)
    _require_recipient(final, first_name=first_name, role=role)
    return _receipt(current, replayed=replayed)


def review_contribution(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: ContributionRole,
    expected_basis: ContributionBasis,
    idempotency_key: str,
) -> ContributionMutationReceipt:
    """Acknowledge the exact current contribution, never mark it clinician-verified."""

    from app.services.visit_prep_patient_handoff import stage_patient_handoff

    if not isinstance(expected_basis, ContributionBasis):
        raise VisitPrepInterviewValidation("contribution basis is invalid")
    key = _canonical_idempotency_key(idempotency_key)
    with _visit_prep_session() as session:
        authorized = _lock_authorized_interview(session, access_token=access_token)
        _require_recipient(authorized, first_name=first_name, role=role)
        prep = authorized.visit_prep
        current = _load_contribution(session, visit_prep=prep)
        fingerprint = contribution_payload_hmac(
            visit_prep_id=prep.id,
            actor_role=role,
            event_kind="review",
            basis=expected_basis,
        )
        if _existing_event(session, prep=prep, key=key, fingerprint=fingerprint):
            stage_patient_handoff(session, access_token=access_token)
            _commit(session)
            return _receipt(current, replayed=True)
        if current.basis != expected_basis or not current.current_answers_complete:
            raise VisitPrepInterviewConflict("Visit Prep contribution state changed")
        try:
            updated = _append_event_in_session(
                session,
                authorized=authorized,
                role=role,
                basis=expected_basis,
                key=key,
                fingerprint=fingerprint,
            )
        except VisitPrepInterviewConflict:
            session.rollback()
            raise
        stage_patient_handoff(session, access_token=access_token)
        _commit(session)
        return _receipt(updated)


def get_patient_contribution(
    *,
    access_token: str | ConfirmedVisitPrepAccess,
    first_name: str,
    role: ContributionRole,
) -> PatientContribution:
    """Read this patient's current contribution after fresh confirmation evidence.

    A bearer alone never discloses saved answers. This uses the invitation's
    existing recipient evidence; it does not claim verified patient identity.
    Explicit wire serializers must exclude internal lineage and hidden topics.
    """

    with _visit_prep_session() as session:
        authorized = _lock_authorized_interview(session, access_token=access_token)
        _require_recipient(authorized, first_name=first_name, role=role)
        current = _load_contribution(session, visit_prep=authorized.visit_prep)
        _commit(session)
        return current
