"""Exact encrypted patient words bound to a retained neutral prompt.

Callers must authorize before decoding. The codec proves payload binding, not
patient identity, action authority, browser presentation, or model provenance.
The patient command supplies those words separately from any model reply.
"""

from dataclasses import dataclass

from app.services.visit_prep_clarification_contract import (
    VisitPrepClarificationContext,
    VisitPrepClarificationInvalid,
    _ref,
    append_patient_clarification,
)
from app.services.visit_prep_clarification_evidence import (
    _INVALID,
    _PROPOSAL_DOMAIN,
    ClarificationProposalEvidence,
    SealedClarificationEvidence,
    _json,
    _read,
    _seal,
    _uuid,
)
from app.utils.encryption import pseudonymous_digest

from app.models.visit_prep_clarification_turn import VisitPrepClarificationTurn

_DOMAIN = "visit-prep-patient-clarification-turn-v1"


@dataclass(frozen=True, repr=False)
class ClarificationTurnEvidence:
    turn_id: str
    operation_id: str
    patient_words: str

    def context(
        self, proposal: ClarificationProposalEvidence
    ) -> VisitPrepClarificationContext:
        if type(proposal) is not ClarificationProposalEvidence:
            raise VisitPrepClarificationInvalid()
        return append_patient_clarification(
            proposal.context, proposal.reply, patient_words=self.patient_words
        )

    def manifest(self, proposal: ClarificationProposalEvidence) -> dict:
        if (
            type(self) is not ClarificationTurnEvidence
            or not _ref(self.turn_id, {"visit_prep_clarification_turn"})
            or not _uuid(self.operation_id)
        ):
            raise VisitPrepClarificationInvalid()
        context = self.context(proposal)
        return {
            "format_version": 1,
            "turn_id": self.turn_id,
            "operation_id": self.operation_id,
            "visit_prep_id": context.package_id,
            "proposal_id": proposal.proposal_id,
            "proposal_hmac_sha256": pseudonymous_digest(
                _json(proposal.manifest()), context=_PROPOSAL_DOMAIN
            ),
            "turn_sequence": len(context.turns),
            "prompt_code": context.turns[-1].prompt_code,
            "patient_words": self.patient_words,
        }

    def __repr__(self) -> str:
        return "ClarificationTurnEvidence(<redacted>)"


def seal_clarification_turn(
    evidence: ClarificationTurnEvidence, *, proposal: ClarificationProposalEvidence
) -> SealedClarificationEvidence:
    result = None
    try:
        if type(evidence) is not ClarificationTurnEvidence:
            raise VisitPrepClarificationInvalid()
        result = _seal(
            evidence.manifest(proposal),
            domain=_DOMAIN,
            max_plain=10_000,
            max_cipher=16_384,
        )
    except _INVALID:
        pass
    if result is None:
        raise VisitPrepClarificationInvalid()
    return result


def read_clarification_turn(
    row: VisitPrepClarificationTurn, *, proposal: ClarificationProposalEvidence
) -> ClarificationTurnEvidence:
    result = None
    try:
        if type(row) is not VisitPrepClarificationTurn:
            raise VisitPrepClarificationInvalid()
        payload = _read(row, domain=_DOMAIN, max_plain=10_000, max_cipher=16_384)
        evidence = ClarificationTurnEvidence(
            str(row.id), row.operation_id, payload["patient_words"]
        )
        manifest = evidence.manifest(proposal)
        if (
            str(row.visit_prep_id) != proposal.context.package_id
            or str(row.proposal_id) != proposal.proposal_id
            or type(row.turn_sequence) is not int
            or row.turn_sequence != manifest["turn_sequence"]
            or _json(payload) != _json(manifest)
        ):
            raise VisitPrepClarificationInvalid()
        result = evidence
    except _INVALID:
        pass
    if result is None:
        raise VisitPrepClarificationInvalid()
    return result
