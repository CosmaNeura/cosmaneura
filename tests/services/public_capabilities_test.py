import json
from pathlib import Path

from app.services.public_capabilities import (
    CapabilityKey,
    CapabilityPolicyFacts,
    RolloutState,
    capability_decisions,
)
from app.services.public_entitlements import CAPABILITY_KEYS

ROOT = Path(__file__).parents[2]


def _facts(**overrides: object) -> CapabilityPolicyFacts:
    values: dict[str, object] = {
        "email_verified": True,
        "membership_state": "active",
        "membership_authorized": True,
        "baa_accepted": True,
        "terms_accepted": True,
        "privacy_accepted": True,
        "legal_required": True,
        "subscription_in_force": True,
        "credential_required": True,
        "credential_state": "approved",
        "public_pilot_cohort": True,
    }
    values.update(overrides)
    return CapabilityPolicyFacts(**values)  # type: ignore[arg-type]


def test_default_policy_has_exact_keys_and_unfinished_tools_off() -> None:
    decisions = capability_decisions(_facts())

    assert tuple(decisions) == CAPABILITY_KEYS
    assert decisions["soap_note"]["decision"] == "allowed"
    for key in ("intake_prep", "session_prep", "scribe", "cab"):
        assert decisions[key]["decision"] == "denied"
        assert decisions[key]["reason_codes"] == ["rollout_disabled"]

    assert decisions["session_prep"]["operation_mode"] == "read_only"
    assert decisions["scribe"]["operation_mode"] == "web_pwa_first"
    assert decisions["cab"]["operation_mode"] == "advisory_only"


def test_pending_credential_decisions_match_the_frozen_contract_fixture() -> None:
    fixture = json.loads(
        (
            ROOT
            / "contracts"
            / "public-entitlements"
            / "v1"
            / "synthetic-credential-pending.json"
        ).read_text()
    )

    assert (
        capability_decisions(_facts(credential_state="pending"))
        == fixture["capabilities"]
    )


def test_every_public_gate_fails_closed() -> None:
    cases = {
        "email_unverified": {"email_verified": False},
        "membership_inactive": {"membership_state": "inactive"},
        "membership_revoked": {"membership_state": "revoked"},
        "terms": {"terms_accepted": False},
        "privacy": {"privacy_accepted": False},
        "baa": {"baa_accepted": False},
        "subscription": {"subscription_in_force": False},
        "authorization": {"membership_authorized": False},
        "credential_needs_review": {"credential_state": "needs_review"},
        "credential_rejected": {"credential_state": "rejected"},
    }

    for overrides in cases.values():
        decision = capability_decisions(_facts(**overrides))["soap_note"]
        assert decision["decision"] == "denied"
        assert decision["data_mode"] == "none"
        assert decision["reason_codes"] != ["allowed"]

    pending = capability_decisions(_facts(credential_state="pending"))["soap_note"]
    assert pending["decision"] == "denied"
    assert pending["data_mode"] == "synthetic_only"
    assert pending["reason_codes"] == ["credential_pending"]


def test_cohort_and_missing_policy_never_grant() -> None:
    cohort_rollouts: dict[CapabilityKey, RolloutState] = {
        "soap_note": "cohort",
        "intake_prep": "disabled",
        "session_prep": "disabled",
        "scribe": "disabled",
        # cab intentionally absent to exercise a configuration drift.
    }
    decisions = capability_decisions(
        _facts(public_pilot_cohort=False),
        rollouts=cohort_rollouts,
    )

    assert decisions["soap_note"]["reason_codes"] == ["cohort_ineligible"]
    assert decisions["cab"]["reason_codes"] == ["policy_unavailable"]


def test_managed_path_can_skip_public_only_legal_and_credential_requirements() -> None:
    decisions = capability_decisions(
        _facts(
            terms_accepted=False,
            privacy_accepted=False,
            legal_required=False,
            credential_required=False,
            credential_state="pending",
        )
    )

    assert decisions["soap_note"]["decision"] == "allowed"
