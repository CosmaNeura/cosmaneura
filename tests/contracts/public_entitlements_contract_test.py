import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.routes.public_onboarding import CapabilityEntitlementResponse

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts" / "public-entitlements" / "v1"
V2_CONTRACT_DIR = ROOT / "contracts" / "public-entitlements" / "v2"
CAPABILITY_OPENAPI_SCHEMAS = (
    "CapabilityEntitlementResponse",
    "CapabilityActorResponse",
    "CapabilityGatesResponse",
    "CapabilitySeatsResponse",
    "CapabilityMapResponse",
    "CapabilityDecisionResponse",
    "CabCapabilityDecisionResponse",
)


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


def test_v1_fixture_pins_catalog_and_fail_closed_credential_policy() -> None:
    schema = json.loads(
        (CONTRACT_DIR / "capability-entitlement.schema.json").read_text()
    )
    fixture = json.loads(
        (CONTRACT_DIR / "synthetic-credential-pending.json").read_text()
    )

    expected = {"soap_note", "intake_prep", "session_prep", "scribe", "cab"}
    assert fixture["contract_version"] == "public-capability-entitlements.v1"
    assert set(fixture["capabilities"]) == expected
    assert set(schema["properties"]["capabilities"]["required"]) == expected
    assert fixture["gates"]["credential_state"] == "pending"
    assert all(
        decision["decision"] == "denied" and decision["data_mode"] != "real_phi"
        for decision in fixture["capabilities"].values()
    )


def test_v1_seat_and_product_modes_are_frozen() -> None:
    fixture = json.loads(
        (CONTRACT_DIR / "synthetic-credential-pending.json").read_text()
    )

    assert fixture["seats"]["maximum_active_clinician_seats"] == 20
    assert fixture["capabilities"]["session_prep"]["operation_mode"] == "read_only"
    assert fixture["capabilities"]["scribe"]["operation_mode"] == "web_pwa_first"
    assert fixture["capabilities"]["cab"]["operation_mode"] == "advisory_only"


def test_v1_preserves_fail_closed_billing_incident_states() -> None:
    schema = json.loads(
        (CONTRACT_DIR / "capability-entitlement.schema.json").read_text()
    )
    states = set(
        schema["properties"]["gates"]["properties"]["subscription_state"]["enum"]
    )

    assert {"pending_verification", "invalid"} <= states


def test_v1_intentionally_excludes_npi_from_every_contract_object() -> None:
    schema = json.loads(
        (CONTRACT_DIR / "capability-entitlement.schema.json").read_text()
    )

    assert "npi" not in _property_names(schema)


def test_v1_schema_remains_byte_pinned() -> None:
    content = (CONTRACT_DIR / "capability-entitlement.schema.json").read_bytes()

    assert hashlib.sha256(content).hexdigest() == (
        "a33ba095ea284575fc8004ecc7b2e05b6aa60c911f5c03add3a0ea1f7ef9e578"
    )


def test_v2_schema_pins_disabled_and_cab_semantics() -> None:
    schema = json.loads(
        (V2_CONTRACT_DIR / "capability-entitlement.schema.json").read_text()
    )
    fixture = json.loads(
        (V2_CONTRACT_DIR / "synthetic-credential-pending.json").read_text()
    )

    assert fixture["contract_version"] == "public-capability-entitlements.v2"
    assert schema["properties"]["contract_version"]["const"] == (
        "public-capability-entitlements.v2"
    )
    assert schema["properties"]["capabilities"]["properties"]["cab"] == {
        "$ref": "#/$defs/cabCapabilityDecision"
    }
    cab_constraints = schema["$defs"]["cabCapabilityDecision"]["allOf"][1]
    assert cab_constraints["properties"]["operation_mode"] == {"const": "advisory_only"}
    disabled_then = schema["$defs"]["capabilityDecision"]["allOf"][0]["then"][
        "properties"
    ]
    assert disabled_then["decision"] == {"const": "denied"}
    assert disabled_then["data_mode"] == {"const": "none"}
    assert disabled_then["reason_codes"]["contains"] == {"const": "rollout_disabled"}
    assert disabled_then["reason_codes"]["not"] == {"contains": {"const": "allowed"}}
    assert "npi" not in _property_names(schema)

    CapabilityEntitlementResponse.model_validate(fixture)


def test_v2_schema_remains_byte_pinned() -> None:
    content = (V2_CONTRACT_DIR / "capability-entitlement.schema.json").read_bytes()

    assert hashlib.sha256(content).hexdigest() == (
        "a51843ed0810a2b43fd16cf6e66eb2830717c8381aa7fea3f8d05b9e55cb1bad"
    )


def test_capability_openapi_projection_remains_receiver_compatible() -> None:
    specification = json.loads((ROOT / "web" / "openapi.json").read_text())
    projection = {
        "path": specification["paths"]["/internal/v1/capabilities"],
        "schemas": {
            name: specification["components"]["schemas"][name]
            for name in CAPABILITY_OPENAPI_SCHEMAS
        },
    }
    canonical = (
        json.dumps(projection, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()

    assert hashlib.sha256(canonical).hexdigest() == (
        "62babdb18afcc3bb9d902d9afb652fd9f6aa20bf35973a1959c52451b7cf9636"
    )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("operation_mode", "standard"),
        ("decision", "allowed"),
        ("data_mode", "real_phi"),
        ("reason_codes", ["credential_pending"]),
        ("reason_codes", ["allowed", "rollout_disabled"]),
    ),
)
def test_v2_runtime_schema_rejects_incoherent_disabled_cab_payloads(
    field: str,
    invalid_value: object,
) -> None:
    fixture = json.loads(
        (V2_CONTRACT_DIR / "synthetic-credential-pending.json").read_text()
    )
    payload = deepcopy(fixture)
    payload["capabilities"]["cab"][field] = invalid_value

    with pytest.raises(ValidationError):
        CapabilityEntitlementResponse.model_validate(payload)
