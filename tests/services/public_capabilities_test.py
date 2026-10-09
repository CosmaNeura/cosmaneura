import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.services.public_capabilities import (
    CapabilityPolicyFacts,
    capability_decisions,
)
from app.services.public_entitlements import CAPABILITY_KEYS

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = (
    ROOT
    / "contracts"
    / "public-entitlements"
    / "v1"
    / "capability-entitlement.schema.json"
)


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _decision_base() -> dict:
    return _schema()["$defs"]["capabilityDecision"]


def _authorized() -> CapabilityPolicyFacts:
    """Every gate satisfied. Individual tests degrade one fact at a time."""

    return CapabilityPolicyFacts(
        identity_verified=True,
        membership_state="active",
        membership_authorized=True,
        role="owner",
        baa_accepted=True,
        terms_accepted=True,
        privacy_accepted=True,
        subscription_in_force=True,
        tenant_isolation_verified=True,
        public_pilot_cohort=True,
    )


def _reason_codes(decision: dict[str, object]) -> list[str]:
    codes = decision["reason_codes"]
    assert isinstance(codes, list)
    assert all(isinstance(code, str) for code in codes)
    return codes


def test_decisions_cover_exactly_the_frozen_capability_catalog() -> None:
    decisions = capability_decisions(_authorized())

    assert set(decisions) == set(CAPABILITY_KEYS)
    assert set(decisions) == set(_schema()["properties"]["capabilities"]["required"])


def test_every_decision_matches_the_v1_wire_contract() -> None:
    base = _decision_base()
    required = set(base["required"])
    enums = {
        field: set(base["properties"][field]["enum"])
        for field in (
            "decision",
            "data_mode",
            "rollout",
            "operation_mode",
            "consent_requirement",
        )
    }
    allowed_reason_codes = set(base["properties"]["reason_codes"]["items"]["enum"])

    for key, decision in capability_decisions(_authorized()).items():
        assert required <= set(decision), key
        for field, permitted in enums.items():
            assert decision[field] in permitted, (key, field, decision[field])
        assert _reason_codes(decision), key
        assert len(set(_reason_codes(decision))) == len(_reason_codes(decision)), key
        assert set(_reason_codes(decision)) <= allowed_reason_codes, key


def test_a_fully_gated_cohort_actor_is_allowed_real_phi() -> None:
    soap_note = capability_decisions(_authorized())["soap_note"]

    assert soap_note["decision"] == "allowed"
    assert soap_note["data_mode"] == "real_phi"
    assert soap_note["rollout"] == "cohort"
    assert _reason_codes(soap_note) == ["allowed"]


def test_default_rollouts_leave_every_unfinished_tool_dark() -> None:
    decisions = capability_decisions(_authorized())

    for key in ("intake_prep", "session_prep", "scribe", "cab"):
        assert decisions[key]["decision"] == "denied", key
        assert decisions[key]["data_mode"] == "none", key
        assert decisions[key]["rollout"] == "disabled", key
        assert "rollout_disabled" in _reason_codes(decisions[key]), key


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        ("identity_verified", False, "identity_unverified"),
        ("membership_state", "inactive", "membership_inactive"),
        ("membership_state", "revoked", "membership_revoked"),
        ("membership_authorized", False, "membership_unauthorized"),
        ("baa_accepted", False, "legal_incomplete"),
        ("terms_accepted", False, "legal_incomplete"),
        ("privacy_accepted", False, "legal_incomplete"),
        ("subscription_in_force", False, "subscription_inactive"),
        ("tenant_isolation_verified", False, "tenant_isolation_failed"),
    ],
)
def test_each_failed_gate_denies_every_capability(
    field: str, value: object, expected_reason: str
) -> None:
    facts = replace(_authorized(), **{field: value})

    decisions = capability_decisions(facts)

    assert decisions, (
        "there is nothing to check, so the assertions below would pass vacuously"
    )
    for key, decision in decisions.items():
        assert decision["decision"] == "denied", key
        assert decision["data_mode"] == "none", key
        assert expected_reason in _reason_codes(decision), key


def test_cohort_rollout_requires_the_explicit_practice_cohort_bit() -> None:
    facts = replace(_authorized(), public_pilot_cohort=False)

    soap_note = capability_decisions(facts)["soap_note"]

    assert soap_note["decision"] == "denied"
    assert _reason_codes(soap_note) == ["cohort_ineligible"]


def test_unknown_rollout_fails_closed_as_policy_unavailable() -> None:
    decisions = capability_decisions(
        _authorized(),
        rollouts={"soap_note": "experimental"},  # type: ignore[dict-item]
    )

    assert decisions, (
        "there is nothing to check, so the assertions below would pass vacuously"
    )
    for key, decision in decisions.items():
        assert decision["decision"] == "denied", key
        assert decision["rollout"] == "disabled", key
        assert "policy_unavailable" in _reason_codes(decision), key


def test_unrecognized_role_fails_closed_rather_than_assuming_workforce() -> None:
    facts = replace(_authorized(), role="auditor")  # type: ignore[arg-type]

    soap_note = capability_decisions(facts)["soap_note"]

    assert soap_note["decision"] == "denied"
    assert "owner_authority_required" in _reason_codes(soap_note)


def test_no_credential_gate_in_capability_policy() -> None:
    """ADR 0025 — credential status is authorization-neutral."""

    fields = set(CapabilityPolicyFacts.__dataclass_fields__)
    assert not any("credential" in field for field in fields)

    for decision in capability_decisions(_authorized()).values():
        assert not any(
            str(code).startswith("credential_") for code in _reason_codes(decision)
        )


def test_no_npi_in_capability_policy() -> None:
    """ADR 0022 — NPI is purpose-bound and never an access condition."""

    fields = set(CapabilityPolicyFacts.__dataclass_fields__)
    assert not any("npi" in field.lower() for field in fields)

    serialized = json.dumps(capability_decisions(_authorized()))
    assert "npi" not in serialized.lower()


def test_cab_advertises_its_advisory_only_boundary() -> None:
    """ADR 0023 — CAB advises and does not transact."""

    cab = capability_decisions(_authorized())["cab"]

    assert cab["operation_mode"] == "advisory_only"
    assert cab["ehr_write"] is False
    assert cab["claim_submission"] is False
    assert cab["clearinghouse_submission"] is False


def test_intake_prep_carries_the_frozen_question_set() -> None:
    question_set = capability_decisions(_authorized())["intake_prep"]["question_set"]

    assert question_set == {
        "version": "intake-prep.questions.v1",
        "question_count": 5,
        "modifiable_by": "authorized_practice_users",
    }
