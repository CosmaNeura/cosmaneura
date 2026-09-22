"""Pure, fail-closed policy for the public capability contract.

Persistence and HTTP adapters collect authoritative facts. This module is the
only place those facts become capability decisions, which keeps browsers and
downstream services from reproducing access policy.

The wire shape is frozen by
``contracts/public-entitlements/v1/capability-entitlement.schema.json``.
Credential and NPI state are outside this contract by ADR 0025 and ADR 0022;
neither appears here, and no reason code may reference them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from app.services.public_entitlements import CAPABILITY_KEYS

CapabilityKey = Literal[
    "soap_note",
    "intake_prep",
    "session_prep",
    "scribe",
    "cab",
]
MembershipState = Literal["active", "inactive", "revoked"]
PracticeRoleName = Literal["owner", "workforce"]
RolloutState = Literal["disabled", "cohort", "enabled"]
ConsentRequirement = Literal["none", "execution_context"]

DEFAULT_CAPABILITY_ROLLOUTS: Mapping[CapabilityKey, RolloutState] = {
    "soap_note": "cohort",
    "intake_prep": "disabled",
    "session_prep": "disabled",
    "scribe": "disabled",
    "cab": "disabled",
}

_OPERATION_MODES: Mapping[CapabilityKey, str] = {
    "soap_note": "standard",
    "intake_prep": "standard",
    "session_prep": "read_only",
    "scribe": "web_pwa_first",
    "cab": "advisory_only",
}

# Every capability in this catalog acts inside a patient encounter, so each one
# carries the execution-context consent requirement rather than none.
_CONSENT_REQUIREMENTS: Mapping[CapabilityKey, ConsentRequirement] = {
    "soap_note": "execution_context",
    "intake_prep": "execution_context",
    "session_prep": "execution_context",
    "scribe": "execution_context",
    "cab": "execution_context",
}

# Capabilities whose decision object carries additional frozen contract facts.
_INTAKE_PREP_QUESTION_SET = {
    "version": "intake-prep.questions.v1",
    "question_count": 5,
    "modifiable_by": "authorized_practice_users",
}

# ADR 0023: CAB advises and does not transact. These are schema-level constants,
# not runtime policy, so that no deployment can flip them on.
_CAB_TRANSACTION_BOUNDARY = {
    "ehr_write": False,
    "claim_submission": False,
    "clearinghouse_submission": False,
}


def capability_rollout_allows(
    capability: CapabilityKey,
    *,
    public_pilot_cohort: bool,
    rollouts: Mapping[CapabilityKey, RolloutState] = DEFAULT_CAPABILITY_ROLLOUTS,
) -> bool:
    """Return the rollout decision shared by HTTP routes and workers."""

    rollout = rollouts.get(capability)
    return rollout == "enabled" or (rollout == "cohort" and public_pilot_cohort)


@dataclass(frozen=True)
class CapabilityPolicyFacts:
    """Authoritative, PHI-free facts about one actor in one practice.

    Deliberately excludes professional credential status and NPI: ADR 0025 makes
    credentials authorization-neutral, and ADR 0022 keeps NPI purpose-bound and
    just-in-time rather than an access condition.
    """

    identity_verified: bool
    membership_state: MembershipState
    membership_authorized: bool
    role: PracticeRoleName
    baa_accepted: bool
    terms_accepted: bool
    privacy_accepted: bool
    subscription_in_force: bool
    tenant_isolation_verified: bool
    public_pilot_cohort: bool


def _base_reason_codes(facts: CapabilityPolicyFacts) -> list[str]:
    """Reasons that deny every capability regardless of its own rollout."""

    reasons: list[str] = []
    if not facts.identity_verified:
        reasons.append("identity_unverified")
    if facts.membership_state == "inactive":
        reasons.append("membership_inactive")
    elif facts.membership_state == "revoked":
        reasons.append("membership_revoked")
    if not facts.membership_authorized:
        reasons.append("membership_unauthorized")
    if facts.role not in ("owner", "workforce"):
        # An unrecognized role cannot be reasoned about, so it fails closed
        # rather than being treated as workforce.
        reasons.append("owner_authority_required")
    if not (facts.baa_accepted and facts.terms_accepted and facts.privacy_accepted):
        reasons.append("legal_incomplete")
    if not facts.subscription_in_force:
        reasons.append("subscription_inactive")
    if not facts.tenant_isolation_verified:
        reasons.append("tenant_isolation_failed")
    return reasons


def _capability_extras(capability: CapabilityKey) -> dict[str, object]:
    if capability == "intake_prep":
        return {"question_set": dict(_INTAKE_PREP_QUESTION_SET)}
    if capability == "cab":
        return dict(_CAB_TRANSACTION_BOUNDARY)
    return {}


def capability_decisions(
    facts: CapabilityPolicyFacts,
    *,
    rollouts: Mapping[CapabilityKey, RolloutState] = DEFAULT_CAPABILITY_ROLLOUTS,
) -> dict[str, dict[str, object]]:
    """Return decisions for exactly the frozen v1 capability keys.

    A missing or unrecognized rollout entry denies with ``policy_unavailable``
    and reports ``disabled`` on the wire, so an unknown rollout can never widen
    access. The defaults leave every unfinished tool dark.
    """

    base_reasons = _base_reason_codes(facts)
    decisions: dict[str, dict[str, object]] = {}

    for key in CAPABILITY_KEYS:
        rollout = rollouts.get(key)  # type: ignore[arg-type]
        reasons = list(base_reasons)

        if rollout not in ("disabled", "cohort", "enabled"):
            reasons.append("policy_unavailable")
            wire_rollout: RolloutState = "disabled"
        else:
            wire_rollout = rollout
            if rollout == "disabled":
                reasons.append("rollout_disabled")
            elif rollout == "cohort" and not facts.public_pilot_cohort:
                reasons.append("cohort_ineligible")

        allowed = not reasons and capability_rollout_allows(
            key,  # type: ignore[arg-type]
            public_pilot_cohort=facts.public_pilot_cohort,
            rollouts=rollouts,
        )

        decision: dict[str, object] = {
            "decision": "allowed" if allowed else "denied",
            "data_mode": "real_phi" if allowed else "none",
            "rollout": wire_rollout,
            "operation_mode": _OPERATION_MODES[key],  # type: ignore[index]
            "consent_requirement": _CONSENT_REQUIREMENTS[key],  # type: ignore[index]
            "reason_codes": ["allowed"] if allowed else reasons,
        }
        decision.update(_capability_extras(key))  # type: ignore[arg-type]
        decisions[key] = decision

    return decisions
