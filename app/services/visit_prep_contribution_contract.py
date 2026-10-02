"""Current patient contribution and exact-basis correction/review fingerprints.

Internal source data, not a public wire response or clinician verification.
Plaintext is deliberately absent from repr; callers must serialize explicitly
only after resolving the applicable patient or provider disclosure authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.services.visit_prep_interview_contract import (
    InterviewQuestion,
    ResponseKind,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
    _CanonicalResponse,
)
from app.utils.encryption import EncryptionError, pseudonymous_digest

from activemodel.types import TypeIDType

ContributionRole = Literal["patient", "caregiver"]
ContributionEventKind = Literal["correction", "review", "topic_answer", "explanation"]


@dataclass(frozen=True)
class ContributionBasis:
    original_response_count: int
    event_sequence: int

    def __post_init__(self) -> None:
        if (
            type(self.original_response_count) is not int
            or not 0 <= self.original_response_count <= 5
            or type(self.event_sequence) is not int
            or self.event_sequence < 0
            or (self.original_response_count == 0 and self.event_sequence != 0)
        ):
            raise VisitPrepInterviewValidation("contribution basis is invalid")


@dataclass(frozen=True)
class ContributionTopic:
    question: InterviewQuestion
    applicable: bool
    response_kind: ResponseKind | None
    answer: str | None = field(repr=False)
    original_answer: str | None = field(repr=False)
    original_response_kind: ResponseKind | None
    original_response_id: TypeIDType | None
    original_recorded_at: datetime | None
    current_correction_id: TypeIDType | None
    recorded_at: datetime | None
    additional_answer_source_id: TypeIDType | None = None
    additional_answer: str | None = field(default=None, repr=False)
    additional_response_kind: ResponseKind | None = None
    additional_recorded_at: datetime | None = None
    explanation_id: TypeIDType | None = None
    explanation_words: str | None = field(default=None, repr=False)
    explanation_recorded_at: datetime | None = None


@dataclass(frozen=True)
class PatientContribution:
    basis: ContributionBasis
    actor_role: ContributionRole
    topics: tuple[ContributionTopic, ...] = field(repr=False)
    original_interview_completed: bool
    current_answers_complete: bool
    current_answers_reviewed: bool
    current_review_id: TypeIDType | None
    reviewed_at: datetime | None

    @property
    def missing_question_keys(self) -> tuple[str, ...]:
        return tuple(
            topic.question.key
            for topic in self.topics
            if topic.applicable and topic.response_kind is None
        )


@dataclass(frozen=True)
class ContributionMutationReceipt:
    """Value-free acknowledgment; never serialize PatientContribution instead."""

    disposition: Literal["accepted", "replayed"]
    basis: ContributionBasis
    current_answers_complete: bool
    current_answers_reviewed: bool


def contribution_payload_hmac(
    *,
    visit_prep_id: TypeIDType,
    actor_role: ContributionRole,
    event_kind: ContributionEventKind,
    basis: ContributionBasis,
    original_response_id: TypeIDType | None = None,
    question_id: TypeIDType | None = None,
    response: _CanonicalResponse | None = None,
    target_revision_id: TypeIDType | None = None,
) -> str:
    """Bind operation, source, actor, answer and exact basis; never plain SHA."""

    if (
        actor_role not in {"patient", "caregiver"}
        or event_kind not in {"correction", "review", "topic_answer", "explanation"}
        or (event_kind != "explanation" and target_revision_id is not None)
        or not isinstance(basis, ContributionBasis)
        or basis.original_response_count == 0
        or (
            event_kind == "explanation"
            and (question_id is None or not isinstance(response, _CanonicalResponse))
        )
        or (
            event_kind == "correction"
            and (
                original_response_id is None
                or question_id is not None
                or not isinstance(response, _CanonicalResponse)
            )
        )
        or (
            event_kind == "topic_answer"
            and (
                original_response_id is not None
                or question_id is None
                or not isinstance(response, _CanonicalResponse)
            )
        )
        or (
            event_kind == "review"
            and (
                original_response_id is not None
                or question_id is not None
                or response is not None
            )
        )
    ):
        raise VisitPrepInterviewValidation("contribution event is invalid")
    payload = json.dumps(
        {
            "version": 1,
            "visit_prep_id": str(visit_prep_id),
            "actor_role": actor_role,
            "event_kind": event_kind,
            "basis_response_count": basis.original_response_count,
            "basis_event_sequence": basis.event_sequence,
            "original_response_id": (
                str(original_response_id) if original_response_id is not None else None
            ),
            "question_key": response.question_key if response else None,
            "response_kind": response.response_kind if response else None,
            "answer": response.answer if response else None,
            # Preserve the original v1 fingerprint bytes for existing events.
            **(
                {"question_id": str(question_id)}
                if event_kind == "topic_answer"
                else {}
            ),
            **(
                {
                    "question_id": str(question_id),
                    "target_revision_id": str(target_revision_id)
                    if target_revision_id
                    else None,
                }
                if event_kind == "explanation"
                else {}
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        return pseudonymous_digest(
            payload, context="visit-prep-contribution-payload-v1"
        )
    except EncryptionError:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from None
