"""BAA acceptance must not keep a signer-supplied client address.

These tests fail while ``accept_practice_baa`` stores the leftmost
``X-Forwarded-For`` token. They pass once the recorded address is limited to
an address the server actually observed: the immediate peer, or the rightmost
forwarded token (the one a trusted proxy appends), and only when that value
is an IP address.

Run, with the repo test environment loaded:

    uv run pytest tests/routes/baa_forwarded_for_acceptance_test.py
"""

from __future__ import annotations

import csv
import io
import ipaddress
from typing import cast

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

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

from tests.routes.conftest import INACTIVE_VIEW_AS_BINDING, VIEW_AS_BINDING_HEADER
from tests.routes.utils import (
    MOCK_CLERK_ISSUER,
    get_clerk_admin_user,
    get_clerk_dev_user,
    grant_test_superadmin_capability,
)

# RFC 5737 documentation ranges. The leftmost values stand for what the signer
# puts in the header. The rightmost value stands for the address a trusted
# proxy appends. Neither is a credential.
_SIGNER_IP = "203.0.113.7"
_SIGNER_MIDDLE_IP = "198.51.100.50"
_APPENDED_IP = "198.51.100.8"
_UNTRUSTED_PEER = "10.1.2.3"
# uvicorn 0.34.2 defaults forwarded_allow_ips to this exact literal.
_TRUSTED_PROXY_PEER = "127.0.0.1"
_SIGNER_TEXT = "signer-supplied-audit-text"

_ACCEPT_BODY = {
    "doc_id": BAA_DOC_ID,
    "version": BAA_CURRENT_VERSION,
    "sha256": BAA_CURRENT_SHA256,
    "signature_name": "Pat Owner",
    "signature_title": "Physician / owner",
    "checks": ["baa"],
}


@pytest.fixture(autouse=True)
def _verified_clerk_email_matches_bound_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return (doctor.email or "").strip().lower() if doctor else None

    monkeypatch.setattr(internal, "verified_primary_email", current_email)


def _doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    assert doctor.email
    return doctor


def _install_production_proxy_headers(client: TestClient, peer: str) -> None:
    """Wrap the app the way ``uvicorn.run(..., proxy_headers=True)`` does.

    ``main.py`` enables proxy headers outside development and does not pass
    ``forwarded_allow_ips``, so uvicorn 0.34.2 trusts only ``127.0.0.1``.
    """

    transport = client._transport
    app = getattr(transport, "app", None)
    if not isinstance(app, ProxyHeadersMiddleware):
        transport.app = ProxyHeadersMiddleware(api_app, trusted_hosts="127.0.0.1")
    transport.client = (peer, 443)


def _create_practice(client: TestClient, legal_name: str) -> None:
    response = client.post(
        api_app.url_path_for("create_practice"),
        json={
            "legal_name": legal_name,
            "is_solo": True,
            "channel": "direct",
            "pastoral_pack": False,
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.text


def _accept(
    client: TestClient,
    forwarded: list[tuple[str, str]] | None,
) -> None:
    headers = list(forwarded or [])
    response = client.post(
        api_app.url_path_for("accept_practice_baa"),
        json=_ACCEPT_BODY,
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK, response.text


def _recorded(doctor: Doctor) -> BaaAcceptance:
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member is not None
    return cast(
        BaaAcceptance,
        BaaAcceptance.where(BaaAcceptance.practice_id == member.practice_id).one(),
    )


def _forwarded_tokens(forwarded: list[tuple[str, str]] | None) -> list[str]:
    tokens: list[str] = []
    for _name, value in forwarded or []:
        tokens.extend(part.strip() for part in value.split(","))
    return [token for token in tokens if token]


def _allowed_recorded_ips(
    peer: str, forwarded: list[tuple[str, str]] | None
) -> set[str]:
    """Peer, plus the rightmost forwarded token when that token is an IP."""

    candidates = [peer]
    tokens = _forwarded_tokens(forwarded)
    if tokens:
        candidates.append(tokens[-1])
    allowed: set[str] = set()
    for candidate in candidates:
        try:
            allowed.add(str(ipaddress.ip_address(candidate)))
        except ValueError:
            continue
    return allowed


def _assert_recorded_address_is_observed(
    stored: str | None,
    *,
    peer: str,
    forwarded: list[tuple[str, str]] | None,
) -> None:
    allowed = _allowed_recorded_ips(peer, forwarded)
    if stored is None:
        return
    observed = str(ipaddress.ip_address(stored))
    assert observed in allowed


def _accept_and_record(
    client: TestClient,
    *,
    peer: str,
    legal_name: str,
    forwarded: list[tuple[str, str]] | None,
) -> BaaAcceptance:
    doctor = _doctor()
    _install_production_proxy_headers(client, peer)
    _create_practice(client, legal_name)
    _accept(client, forwarded)
    record = _recorded(doctor)
    assert (
        BaaAcceptance.where(BaaAcceptance.practice_id == record.practice_id).count()
        == 1
    )
    _assert_recorded_address_is_observed(
        record.ip_address, peer=peer, forwarded=forwarded
    )
    return record


@pytest.mark.parametrize(
    ("peer", "forwarded_value"),
    [
        (
            _UNTRUSTED_PEER,
            f"{_SIGNER_IP}, {_SIGNER_MIDDLE_IP}, {_APPENDED_IP}",
        ),
        (
            _UNTRUSTED_PEER,
            f"  {_SIGNER_IP}  ,  {_APPENDED_IP}  ",
        ),
        (
            _UNTRUSTED_PEER,
            f"{_SIGNER_TEXT}, {_APPENDED_IP}",
        ),
        (
            _UNTRUSTED_PEER,
            f"{_SIGNER_IP}:443, {_APPENDED_IP}",
        ),
        (
            _TRUSTED_PROXY_PEER,
            f"{_SIGNER_IP}, {_SIGNER_MIDDLE_IP}, {_APPENDED_IP}",
        ),
    ],
)
def test_baa_acceptance_does_not_store_signer_supplied_forwarded_for(
    bare_authenticated_client: TestClient,
    peer: str,
    forwarded_value: str,
) -> None:
    _accept_and_record(
        bare_authenticated_client,
        peer=peer,
        legal_name="Observed Address Practice, PLLC",
        forwarded=[("x-forwarded-for", forwarded_value)],
    )


def test_duplicate_forwarded_for_headers_do_not_keep_the_first(
    bare_authenticated_client: TestClient,
) -> None:
    _accept_and_record(
        bare_authenticated_client,
        peer=_UNTRUSTED_PEER,
        legal_name="Duplicate Forwarded For Practice, PLLC",
        forwarded=[
            ("x-forwarded-for", f"{_SIGNER_IP}, {_SIGNER_MIDDLE_IP}"),
            ("x-forwarded-for", _APPENDED_IP),
        ],
    )


def test_absent_forwarded_for_records_the_immediate_peer(
    bare_authenticated_client: TestClient,
) -> None:
    record = _accept_and_record(
        bare_authenticated_client,
        peer=_UNTRUSTED_PEER,
        legal_name="Peer Only Practice, PLLC",
        forwarded=None,
    )
    assert record.ip_address == _UNTRUSTED_PEER


def test_retry_does_not_replace_the_recorded_address(
    bare_authenticated_client: TestClient,
) -> None:
    forwarded = [("x-forwarded-for", f"{_SIGNER_IP}, {_APPENDED_IP}")]
    record = _accept_and_record(
        bare_authenticated_client,
        peer=_UNTRUSTED_PEER,
        legal_name="Immutable Address Practice, PLLC",
        forwarded=forwarded,
    )
    original = record.ip_address
    _accept(
        bare_authenticated_client,
        [("x-forwarded-for", f"{_APPENDED_IP}, {_UNTRUSTED_PEER}")],
    )
    record.refresh()
    assert record.ip_address == original
    assert (
        BaaAcceptance.where(BaaAcceptance.practice_id == record.practice_id).count()
        == 1
    )


def _enable_staging_admin(monkeypatch: pytest.MonkeyPatch) -> Doctor:
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_clerk_issuer",
        lambda: MOCK_CLERK_ISSUER,
    )
    _, _, clerk_admin = get_clerk_admin_user()
    admin = Doctor.get(clerk_id=clerk_admin.id)
    assert admin is not None
    grant_test_superadmin_capability(admin, SuperadminCapabilityKind.platform_admin)
    return admin


def test_admin_export_publishes_the_observed_address_only(
    bare_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legal_name = "Exported Address Practice, PLLC"
    forwarded = [("x-forwarded-for", f"{_SIGNER_TEXT}, {_SIGNER_IP}, {_APPENDED_IP}")]
    doctor = _doctor()
    _install_production_proxy_headers(bare_authenticated_client, _UNTRUSTED_PEER)
    _create_practice(bare_authenticated_client, legal_name)
    _accept(bare_authenticated_client, forwarded)
    record = _recorded(doctor)
    _enable_staging_admin(monkeypatch)
    _, _, clerk_admin = get_clerk_admin_user()
    bare_authenticated_client.override_user(clerk_admin)  # type: ignore[attr-defined]
    bare_authenticated_client.headers[VIEW_AS_BINDING_HEADER] = INACTIVE_VIEW_AS_BINDING

    json_response = bare_authenticated_client.get(
        api_app.url_path_for("baa_acceptances_export")
    )
    assert json_response.status_code == status.HTTP_200_OK, json_response.text
    exported = [
        row for row in json_response.json() if row["practice_legal_name"] == legal_name
    ]
    assert len(exported) == 1
    assert exported[0]["ip_address"] == record.ip_address
    _assert_recorded_address_is_observed(
        exported[0]["ip_address"],
        peer=_UNTRUSTED_PEER,
        forwarded=forwarded,
    )
    assert _SIGNER_TEXT not in json_response.text
    assert _SIGNER_IP not in json_response.text

    csv_response = bare_authenticated_client.get(
        api_app.url_path_for("baa_acceptances_export"),
        params={"format": "csv"},
    )
    assert csv_response.status_code == status.HTTP_200_OK, csv_response.text
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    exported_csv = [row for row in rows if row["practice_legal_name"] == legal_name]
    assert len(exported_csv) == 1
    assert exported_csv[0]["ip_address"] == record.ip_address
    assert _SIGNER_TEXT not in csv_response.text
    assert _SIGNER_IP not in csv_response.text
