"""Pure, fail-closed policy for the public capability contract.

Persistence and HTTP adapters collect authoritative facts.  This module is the
only place those facts become capability decisions, which keeps browser and
downstream clients from reproducing access policy.
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
CredentialState = Literal["pending", "approved", "rejected", "needs_review"]
RolloutState = Literal["disabled", "cohort", "enabled"]

DEFAULT_CAPABILITY_ROLLOUTS: Mapping[CapabilityKey, RolloutState] = {
    "soap_note": "enabled",
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
    email_verified: bool
    membership_state: MembershipState
    membership_authorized: bool
    baa_accepted: bool
    terms_accepted: bool
    privacy_accepted: bool
    legal_required: bool
    subscription_in_force: bool
    credential_required: bool
    credential_state: CredentialState
    public_pilot_cohort: bool


def _base_reason_codes(facts: CapabilityPolicyFacts) -> list[str]:
    reasons: list[str] = []
    if not facts.email_verified:
        reasons.append("email_unverified")
    if facts.membership_state == "inactive":
        reasons.append("membership_inactive")
    elif facts.membership_state == "revoked":
        reasons.append("membership_revoked")
    if not facts.membership_authorized:
        # V1 uses policy_unavailable as its fail-closed reason when the
        # underlying authorization evidence is absent or cannot be resolved.
        reasons.append("policy_unavailable")
    if facts.legal_required and not (
        facts.baa_accepted and facts.terms_accepted and facts.privacy_accepted
    ):
        reasons.append("legal_incomplete")
    elif not facts.baa_accepted:
        # Managed paths can use negotiated legal instruments, but still never
        # receive PHI access without an effective BAA fact.
        reasons.append("legal_incomplete")
    if not facts.subscription_in_force:
        reasons.append("subscription_inactive")
    if facts.credential_required and facts.credential_state != "approved":
        reasons.append(f"credential_{facts.credential_state}")
    return reasons


def capability_decisions(
    facts: CapabilityPolicyFacts,
    *,
    rollouts: Mapping[CapabilityKey, RolloutState] = DEFAULT_CAPABILITY_ROLLOUTS,
) -> dict[str, dict[str, object]]:
    """Return decisions for exactly the frozen v1 capability keys.

    A missing/unknown rollout entry denies with ``policy_unavailable``.  The
    default leaves every unfinished tool dark.  A cohort rollout additionally
    requires the explicit practice cohort bit; channel or client flags never
    substitute for it.
    """

    base_reasons = _base_reason_codes(facts)
    decisions: dict[str, dict[str, object]] = {}
    for raw_key in CAPABILITY_KEYS:
        key = raw_key  # Literal narrowing is not required at runtime.
        rollout = rollouts.get(key)  # type: ignore[arg-type]
        reasons = list(base_reasons)
        if rollout not in {"disabled", "cohort", "enabled"}:
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
        if allowed:
            reasons = ["allowed"]
        synthetic_only = (
            key == "soap_note"
            and wire_rollout in {"cohort", "enabled"}
            and reasons == ["credential_pending"]
        )
        decisions[key] = {
            "decision": "allowed" if allowed else "denied",
            "data_mode": (
                "real_phi"
                if allowed
                else "synthetic_only"
                if synthetic_only
                else "none"
            ),
            "rollout": wire_rollout,
            "operation_mode": _OPERATION_MODES[key],  # type: ignore[index]
            "reason_codes": reasons,
        }
    return decisions
