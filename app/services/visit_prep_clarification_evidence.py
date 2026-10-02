"""Closed encrypted evidence, not a patient-action or conversational authority.

Readers require prior authorization by their caller. Proposal content must be
persisted before disclosure, and an actual patient decision must reference that
saved proposal, never a client-supplied replacement. These codecs verify exact
bindings and quoted words; they do not prove browser receipt or model quality.
"""

from __future__ import annotations

import hmac
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from app.services.visit_prep_clarification_contract import (
    PROMPT_VERSION,
    DecisionAction,
    PatientClarificationDecision,
    PatientClarificationReply,
    PatientClarificationTurn,
    VisitPrepClarificationContext,
    VisitPrepClarificationInvalid,
    _ref,
    _words,
    validate_clarification_reply,
)
from app.services.visit_prep_contribution_contract import ContributionBasis
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

from app.models.visit_prep_clarification import (
    VisitPrepClarificationDecision,
    VisitPrepClarificationProposal,
)
from app.models.visit_prep_clarification_turn import VisitPrepClarificationTurn

_PROPOSAL_DOMAIN = "visit-prep-clarification-proposal-v1"
_DECISION_DOMAIN = "visit-prep-clarification-decision-v1"
_INVALID = (
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    UnicodeError,
    RecursionError,
    OverflowError,
    EncryptionError,
    DecryptionError,
)


def _json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    parsed = UUID(value)
    return parsed.version == 4 and str(parsed) == value


def _closed(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise VisitPrepClarificationInvalid()
        result[key] = value
    return result


@dataclass(frozen=True, repr=False)
class ClarificationProposalEvidence:
    proposal_id: str
    owner_practice_member_id: str
    question_id: str
    credential_generation: int
    operation_id: str
    model_profile: str
    context: VisitPrepClarificationContext
    reply: PatientClarificationReply
    source_turn_id: str | None = None

    def manifest(self) -> dict:
        if (
            type(self) is not ClarificationProposalEvidence
            or not _ref(self.proposal_id, {"visit_prep_clarification"})
            or not _ref(self.owner_practice_member_id, {"practice_member"})
            or not _ref(self.question_id, {"visit_prep_question"})
            or type(self.credential_generation) is not int
            or not 0 < self.credential_generation <= 2_147_483_647
            or not _uuid(self.operation_id)
            or type(self.model_profile) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.model_profile)
            is None
        ):
            raise VisitPrepClarificationInvalid()
        validate_clarification_reply(self.context, self.reply)
        if self.source_turn_id is not None and (
            not _ref(self.source_turn_id, {"visit_prep_clarification_turn"})
            or not self.context.turns
        ):
            raise VisitPrepClarificationInvalid()
        context_payload = asdict(self.context)
        if self.context.explanation_words is None:
            # Preserve the exact v1 envelope bytes for retained text proposals.
            context_payload.pop("explanation_words")
            context_payload.pop("explanation_source_ref")
        payload = {
            "format_version": 1,
            "prompt_version": PROMPT_VERSION,
            "proposal_id": self.proposal_id,
            "owner_practice_member_id": self.owner_practice_member_id,
            "question_id": self.question_id,
            "credential_generation": self.credential_generation,
            "operation_id": self.operation_id,
            "model_profile": self.model_profile,
            "context": context_payload
            | {"turns": [asdict(turn) for turn in self.context.turns]},
            "reply": self.reply.model_dump(mode="json", warnings="error"),
        }
        if self.source_turn_id is not None:
            payload["source_turn_id"] = self.source_turn_id
        return payload

    def __repr__(self) -> str:
        return "ClarificationProposalEvidence(<redacted>)"


@dataclass(frozen=True, repr=False)
class ClarificationDecisionEvidence:
    decision_id: str
    operation_id: str
    decision: PatientClarificationDecision
    correction_id: str | None

    def manifest(self, proposal: ClarificationProposalEvidence) -> dict:
        if type(proposal) is not ClarificationProposalEvidence:
            raise VisitPrepClarificationInvalid()
        binding = pseudonymous_digest(
            _json(proposal.manifest()), context=_PROPOSAL_DOMAIN
        )
        if (
            type(self) is not ClarificationDecisionEvidence
            or not _ref(self.decision_id, {"visit_prep_clarification_decision"})
            or not _uuid(self.operation_id)
            or type(self.decision) is not PatientClarificationDecision
            or type(self.decision.action) is not str
            or self.decision.action
            not in {"confirm", "edit", "keep_original", "dismiss"}
        ):
            raise VisitPrepClarificationInvalid()
        organized = validate_clarification_reply(proposal.context, proposal.reply)
        if self.decision.action in {"confirm", "edit"}:
            if (
                proposal.reply.kind != "organize"
                or (
                    proposal.context.kind == "choice"
                    and any(
                        span.source == "answer" for span in proposal.reply.statements
                    )
                )
                or organized is None
                or not _ref(self.correction_id, {"visit_prep_contribution"})
            ):
                raise VisitPrepClarificationInvalid()
            if self.decision.action == "edit":
                _words(self.decision.edited_words)
            elif self.decision.edited_words is not None:
                raise VisitPrepClarificationInvalid()
        elif self.correction_id is not None or self.decision.edited_words is not None:
            raise VisitPrepClarificationInvalid()
        return {
            "format_version": 1,
            "decision_id": self.decision_id,
            "operation_id": self.operation_id,
            "proposal_id": proposal.proposal_id,
            "visit_prep_id": proposal.context.package_id,
            "proposal_hmac_sha256": binding,
            "action": self.decision.action,
            "edited_words": self.decision.edited_words,
            "correction_id": self.correction_id,
        }

    def __repr__(self) -> str:
        return "ClarificationDecisionEvidence(<redacted>)"


@dataclass(frozen=True, repr=False)
class SealedClarificationEvidence:
    ciphertext: str
    payload_hmac_sha256: str

    def __repr__(self) -> str:
        return "SealedClarificationEvidence(<redacted>)"


def _seal(
    payload: dict, *, domain: str, max_plain: int, max_cipher: int
) -> SealedClarificationEvidence:
    plaintext = _json(payload)
    if len(plaintext.encode("utf-8")) > max_plain:
        raise VisitPrepClarificationInvalid()
    digest = pseudonymous_digest(plaintext, context=domain)
    ciphertext = encrypt_value(plaintext)
    if not 1 <= len(ciphertext.encode("utf-8")) <= max_cipher:
        raise VisitPrepClarificationInvalid()
    return SealedClarificationEvidence(ciphertext, digest)


def seal_clarification_proposal(
    evidence: ClarificationProposalEvidence,
) -> SealedClarificationEvidence:
    result = None
    try:
        if type(evidence) is not ClarificationProposalEvidence:
            raise VisitPrepClarificationInvalid()
        result = _seal(
            evidence.manifest(),
            domain=_PROPOSAL_DOMAIN,
            max_plain=47_000,
            max_cipher=65_536,
        )
    except _INVALID:
        pass
    if result is None:
        raise VisitPrepClarificationInvalid()
    return result


def seal_clarification_decision(
    evidence: ClarificationDecisionEvidence, *, proposal: ClarificationProposalEvidence
) -> SealedClarificationEvidence:
    result = None
    try:
        if type(evidence) is not ClarificationDecisionEvidence:
            raise VisitPrepClarificationInvalid()
        result = _seal(
            evidence.manifest(proposal),
            domain=_DECISION_DOMAIN,
            max_plain=10_000,
            max_cipher=16_384,
        )
    except _INVALID:
        pass
    if result is None:
        raise VisitPrepClarificationInvalid()
    return result


def _read(
    row: VisitPrepClarificationProposal
    | VisitPrepClarificationDecision
    | VisitPrepClarificationTurn,
    *,
    domain: str,
    max_plain: int,
    max_cipher: int,
) -> dict:
    if (
        type(row.envelope_format_version) is not int
        or row.envelope_format_version != 1
        or type(row.evidence_ciphertext) is not str
        or not 1 <= len(row.evidence_ciphertext.encode("utf-8")) <= max_cipher
        or type(row.payload_hmac_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", row.payload_hmac_sha256) is None
        or type(row.recorded_at) is not datetime
        or row.recorded_at.utcoffset() is None
        or type(row.created_at) is not datetime
        or row.created_at.utcoffset() is None
        or row.created_at != row.recorded_at
    ):
        raise VisitPrepClarificationInvalid()
    plaintext = decrypt_value(row.evidence_ciphertext)
    if len(plaintext.encode("utf-8")) > max_plain:
        raise VisitPrepClarificationInvalid()
    payload = json.loads(plaintext, object_pairs_hook=_closed)
    if type(payload) is not dict or not hmac.compare_digest(
        pseudonymous_digest(_json(payload), context=domain), row.payload_hmac_sha256
    ):
        raise VisitPrepClarificationInvalid()
    return payload


def read_clarification_proposal(
    row: VisitPrepClarificationProposal,
) -> ClarificationProposalEvidence:
    result = None
    try:
        if type(row) is not VisitPrepClarificationProposal:
            raise VisitPrepClarificationInvalid()
        payload = _read(
            row, domain=_PROPOSAL_DOMAIN, max_plain=47_000, max_cipher=65_536
        )
        raw = payload["context"]
        if type(raw) is not dict or type(raw["turns"]) is not list:
            raise VisitPrepClarificationInvalid()
        context = VisitPrepClarificationContext(
            **(
                raw
                | {
                    "basis": ContributionBasis(**raw["basis"]),
                    "turns": tuple(
                        PatientClarificationTurn(**turn) for turn in raw["turns"]
                    ),
                }
            )
        )
        source = str(row.source_contribution_id or row.source_response_id)
        if (
            context.package_id != str(row.visit_prep_id)
            or context.question_set_version_id != str(row.question_set_version_id)
            or context.actor_role != row.actor_role
            or type(row.basis_response_count) is not int
            or type(row.basis_event_sequence) is not int
            or context.basis.original_response_count != row.basis_response_count
            or context.basis.event_sequence != row.basis_event_sequence
            or context.source_version != row.source_version
            or context.current_source_ref != source
            or (row.source_response_id is None) == (row.source_contribution_id is None)
            or (
                row.source_response_id is not None
                and not _ref(str(row.source_response_id), {"visit_prep_response"})
            )
            or (
                row.source_contribution_id is not None
                and not _ref(
                    str(row.source_contribution_id), {"visit_prep_contribution"}
                )
            )
        ):
            raise VisitPrepClarificationInvalid()
        checked = ClarificationProposalEvidence(
            proposal_id=str(row.id),
            owner_practice_member_id=str(row.owner_practice_member_id),
            question_id=str(row.question_id),
            credential_generation=row.credential_generation,
            operation_id=row.operation_id,
            model_profile=payload["model_profile"],
            context=context,
            reply=PatientClarificationReply.model_validate_json(
                _json(payload["reply"])
            ),
            source_turn_id=str(row.source_turn_id)
            if row.source_turn_id is not None
            else None,
        )
        if _json(checked.manifest()) != _json(payload):
            raise VisitPrepClarificationInvalid()
        result = checked
    except _INVALID:
        pass
    if result is None:
        raise VisitPrepClarificationInvalid()
    return result


def read_clarification_decision(
    row: VisitPrepClarificationDecision, *, proposal: ClarificationProposalEvidence
) -> ClarificationDecisionEvidence:
    result = None
    try:
        if type(row) is not VisitPrepClarificationDecision:
            raise VisitPrepClarificationInvalid()
        payload = _read(
            row, domain=_DECISION_DOMAIN, max_plain=10_000, max_cipher=16_384
        )
        if (
            str(row.proposal_id) != proposal.proposal_id
            or str(row.visit_prep_id) != proposal.context.package_id
        ):
            raise VisitPrepClarificationInvalid()
        checked = ClarificationDecisionEvidence(
            decision_id=str(row.id),
            operation_id=row.operation_id,
            decision=PatientClarificationDecision(
                cast(DecisionAction, row.action), payload["edited_words"]
            ),
            correction_id=str(row.correction_id)
            if row.correction_id is not None
            else None,
        )
        if _json(checked.manifest(proposal)) != _json(payload):
            raise VisitPrepClarificationInvalid()
        result = checked
    except _INVALID:
        pass
    if result is None:
        raise VisitPrepClarificationInvalid()
    return result
