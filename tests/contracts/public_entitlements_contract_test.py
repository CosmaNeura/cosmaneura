import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts" / "public-entitlements" / "v1"


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
