import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts" / "public-entitlements" / "v1"
SCHEMA_PATH = CONTRACT_DIR / "capability-entitlement.schema.json"
FIXTURE_PATH = CONTRACT_DIR / "synthetic-authorized-preflight.json"
OPENAPI_PATH = ROOT / "web" / "openapi.json"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _property_names(value: object) -> set[str]:
    if isinstance(value, dict):
        names = set(value.get("properties", {}))
        for child in value.values():
            names.update(_property_names(child))
        return names
    if isinstance(value, list):
        names: set[str] = set()
        for child in value:
            names.update(_property_names(child))
        return names
    return set()


def test_v1_fixture_pins_catalog_and_authorization_neutral_policy() -> None:
    schema = _schema()
    fixture = _fixture()

    expected = {"soap_note", "intake_prep", "session_prep", "scribe", "cab"}
    assert fixture["contract_version"] == "public-capability-entitlements.v1"
    assert fixture["policy_revision"] == "2026-08-28"
    assert set(fixture["capabilities"]) == expected
    assert set(schema["properties"]["capabilities"]["required"]) == expected
    assert fixture["actor"]["membership_state"] == "active"
    assert all(
        fixture["gates"][gate]
        for gate in (
            "identity_verified",
            "membership_authorized",
            "baa_accepted",
            "terms_accepted",
            "privacy_accepted",
            "tenant_isolation_verified",
        )
    )

    property_names = _property_names(schema)
    assert "npi" not in property_names
    assert "credential_required" not in property_names
    assert "credential_state" not in property_names
    reason_codes = set(
        schema["$defs"]["capabilityDecision"]["properties"]["reason_codes"]["items"][
            "enum"
        ]
    )
    assert not any(code.startswith("credential_") for code in reason_codes)


def test_v1_allowed_decisions_require_all_noncredential_gates() -> None:
    schema = _schema()
    allowed_then = schema["allOf"][0]["then"]["properties"]

    assert allowed_then["actor"]["properties"]["membership_state"] == {
        "const": "active"
    }
    gate_constraints = allowed_then["gates"]["properties"]
    assert gate_constraints["subscription_state"] == {"enum": ["trialing", "active"]}
    assert all(
        gate_constraints[name] == {"const": True}
        for name in (
            "identity_verified",
            "membership_authorized",
            "baa_accepted",
            "terms_accepted",
            "privacy_accepted",
            "tenant_isolation_verified",
        )
    )

    allowed_decision = schema["$defs"]["capabilityDecision"]["allOf"][0]["then"][
        "properties"
    ]
    assert allowed_decision["data_mode"] == {"const": "real_phi"}
    assert allowed_decision["rollout"] == {"enum": ["cohort", "enabled"]}
    assert allowed_decision["reason_codes"] == {
        "minItems": 1,
        "maxItems": 1,
        "contains": {"const": "allowed"},
    }


def test_v1_disabled_and_denied_decisions_are_fail_closed() -> None:
    constraints = _schema()["$defs"]["capabilityDecision"]["allOf"]
    denied = constraints[1]["then"]["properties"]
    disabled = constraints[2]["then"]["properties"]

    assert denied["data_mode"] == {"enum": ["none", "synthetic_only"]}
    assert denied["reason_codes"] == {"not": {"contains": {"const": "allowed"}}}
    assert disabled["decision"] == {"const": "denied"}
    assert disabled["data_mode"] == {"const": "none"}
    assert disabled["reason_codes"]["contains"] == {"const": "rollout_disabled"}
    assert disabled["reason_codes"]["not"] == {"contains": {"const": "allowed"}}


def test_v1_seat_and_product_modes_are_frozen() -> None:
    schema = _schema()
    fixture = _fixture()

    assert fixture["seats"]["maximum_active_clinician_seats"] == 20
    assert fixture["capabilities"]["session_prep"]["operation_mode"] == "read_only"
    assert fixture["capabilities"]["scribe"]["operation_mode"] == "web_pwa_first"
    intake = fixture["capabilities"]["intake_prep"]["question_set"]
    assert intake == {
        "version": "intake-prep.questions.v1",
        "question_count": 5,
        "modifiable_by": "authorized_practice_users",
    }

    refs = schema["properties"]["capabilities"]["properties"]
    assert refs["session_prep"] == {"$ref": "#/$defs/readOnlyCapabilityDecision"}
    assert refs["scribe"] == {"$ref": "#/$defs/webPwaFirstCapabilityDecision"}
    assert refs["intake_prep"] == {"$ref": "#/$defs/intakePrepCapabilityDecision"}


@pytest.mark.parametrize(
    "field",
    ("ehr_write", "claim_submission", "clearinghouse_submission"),
)
def test_v1_cab_is_schema_level_advisory_only(field: str) -> None:
    schema = _schema()
    cab = schema["$defs"]["advisoryOnlyCapabilityDecision"]["allOf"][1]

    assert cab["properties"]["operation_mode"] == {"const": "advisory_only"}
    assert cab["properties"][field] == {"const": False}
    assert _fixture()["capabilities"]["cab"][field] is False


def test_v1_preserves_fail_closed_billing_incident_states() -> None:
    states = set(
        _schema()["properties"]["gates"]["properties"]["subscription_state"]["enum"]
    )

    assert {"pending_verification", "invalid"} <= states


def test_v1_schema_and_fixture_are_byte_pinned() -> None:
    assert hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest() == (
        "02f13fcf8226e6990389a085dfa3478ca4d7d4a8f9cc9f5ad3b3e676508fb865"
    )
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == (
        "9695b1a93b0c422467e46a93f587af2224b5b8c40b3c4951fc3bfc432e678530"
    )
    assert hashlib.sha256(OPENAPI_PATH.read_bytes()).hexdigest() == (
        "69ec58152b73f501ad137f101f710f3834722457b9ebc062be7c4348af13f125"
    )
