"""Real private routes and Core SQL, with normalized synthetic auth seams."""

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.configuration.scribe_activation import ScribeActivationSettings
from app.routes import scribe_activation as activation_route
from app.routes import scribe_cancellation as cancellation_route
from app.routes import scribe_provider_context as context_route
from app.routes.dependencies.entra_service import EntraServicePrincipal
from app.routes.embed_context import VerifiedClerkIdentity
from app.services import (
    scribe_account_setup,
    scribe_native_activation,
    scribe_provider_context,
)
from app.services.companion_records import RecordBudget
from app.services.scribe_activation_contract import ActivationRequest
from app.services.scribe_cancellation import digest
from app.services.scribe_cancellation_contract import (
    CancellationPolicy,
    CancellationSettings,
)

from app.models.authority import AuthorityAuditEvent
from app.models.doctor import Doctor
from app.models.practice import Practice
from app.models.scribe_cancellation import scribe_cancellation_operation
from sqlalchemy import event
from sqlmodel import select

from tests.authority_kernel.companion_provider_provisioning_test import (
    SETTINGS,
    case,
    session_for,
)
from tests.authority_kernel.companion_provider_provisioning_test import (
    engine as companion_engine,
)
from tests.authority_kernel.scribe_activation_test import (
    activate,
    grant,
    rebind_subject_after_link,
    require_row,
    snapshot,
)

engine = companion_engine


@pytest.mark.parametrize("revoked", [False, True])
def test_historical_subject_cannot_activate_through_private_http(
    engine, monkeypatch, revoked
):
    args = case(engine)
    rebind_subject_after_link(engine, args, revoked=revoked)
    http, _ = client(engine, monkeypatch, args)
    before = snapshot(engine)
    for path, action in [
        (context_route.PATH, "connection"),
        (activation_route.PATH, "activate"),
    ]:
        response = http.post(
            path,
            json={"action": action, "request_nonce": str(uuid4())},
            headers=headers(key=action == "activate"),
        )
        assert response.status_code == 403
    assert snapshot(engine) == before


def test_unmapped_setup_classification_does_not_take_row_locks(engine, monkeypatch):
    args = case(engine)
    http, _ = client(engine, monkeypatch, args)
    before = snapshot(engine)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = http.post(
            context_route.PATH,
            json={"action": "connection", "request_nonce": str(uuid4())},
            headers=headers(),
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 409 and response.json()["setup"] == "activation"
    assert statements and not any(
        "FOR UPDATE" in statement.upper() for statement in statements
    )
    assert snapshot(engine) == before


def test_cancelled_subject_cannot_be_reclassified_after_doctor_rebind(
    engine, monkeypatch
):
    args = case(engine)
    subject = args["verified_clerk_subject"]
    now = datetime.now(UTC)
    with session_for(engine) as session:
        doctor = require_row(session, Doctor, args["doctor_id"])
        doctor.clerk_id = "user_synthetic_rebound_" + uuid4().hex
        session.execute(
            scribe_cancellation_operation.insert().values(
                id=uuid4(),
                caller_sha256=digest([SETTINGS.clerk_issuer, subject]),
                doctor_ref=str(doctor.id),
                request_key_sha256="a" * 64,
                preview_sha256="b" * 64,
                inventory_sha256="c" * 64,
                inventory=[],
                policy={
                    "grace_days": 90,
                    "phi_scope": "scribe-processing-copies",
                    "approval_sha256": "d" * 64,
                },
                accepted_at=now,
                export_until=now + timedelta(days=90),
                phase="read-only",
            )
        )
        session.commit()
    http, _ = client(engine, monkeypatch, args, subject=subject)
    before = snapshot(engine)
    for path, action in [
        (context_route.PATH, "connection"),
        (activation_route.PATH, "activate"),
        (cancellation_route.PATH, "status"),
    ]:
        response = http.post(
            path,
            json={"action": action, "request_nonce": str(uuid4())},
            headers=headers(key=action == "activate"),
        )
        assert response.status_code == 403
    assert snapshot(engine) == before


def client(engine, monkeypatch, args, *, subject=None, enabled=True):
    app = FastAPI()
    activation_route.install(app)
    context_route.install(app)
    cancellation_route.install(app)
    policy = ScribeActivationSettings(
        enabled=enabled,
        practice_refs=frozenset({str(args["practice_id"])}),
        authority=SETTINGS,
    )
    app.dependency_overrides[activation_route.authenticate_native_workload] = (
        lambda: EntraServicePrincipal("11111111-1111-4111-8111-111111111111")
    )
    app.dependency_overrides[activation_route.authenticate_embed_platform_session] = (
        lambda: VerifiedClerkIdentity(
            SETTINGS.clerk_issuer,
            subject or args["verified_clerk_subject"],
            "sess_synthetic",
        )
    )
    app.dependency_overrides[activation_route.get_authority_kernel_settings] = (
        lambda: SETTINGS
    )
    app.dependency_overrides[activation_route.activation_settings] = lambda: policy
    app.dependency_overrides[context_route.context_enabled] = lambda: True
    app.dependency_overrides[cancellation_route.cancellation_settings] = (
        lambda: CancellationSettings(
            True,
            CancellationPolicy(
                grace_days=90,
                phi_scope="core-and-scribe-clinical-records",
                approval_sha256="a" * 64,
            ),
            ledger_ready=True,
        )
    )
    monkeypatch.setattr(scribe_account_setup, "SCRIBE_ACTIVATION_SETTINGS", policy)
    monkeypatch.setattr(
        scribe_provider_context, "global_session", lambda: session_for(engine)
    )
    monkeypatch.setattr(
        scribe_native_activation, "global_session", lambda: session_for(engine)
    )
    # The cancellation executor lazily imports the same owned-session seam.
    monkeypatch.setattr(
        "activemodel.session_manager.global_session", lambda: session_for(engine)
    )
    return TestClient(app), policy


def headers(*, key=False):
    return {
        "X-Cosmaneura-Record-Version": "1",
        **({"Idempotency-Key": str(uuid4())} if key else {}),
    }


def test_never_activated_status_and_setup_are_nonmutating(engine, monkeypatch):
    args = case(engine)
    http, _ = client(
        engine, monkeypatch, args, subject="user_synthetic_fresh_" + uuid4().hex
    )
    before = snapshot(engine)
    for action, expected in [("status", 404), ("preview", 403)]:
        response = http.post(
            cancellation_route.PATH,
            json={"action": action, "request_nonce": str(uuid4())},
            headers=headers(),
        )
        assert response.status_code == expected
    nonce = str(uuid4())
    response = http.post(
        context_route.PATH,
        json={"action": "connection", "request_nonce": nonce},
        headers=headers(),
    )
    assert response.status_code == 409 and response.json() == {
        "type": "scribe_account_setup_required",
        "status": 409,
        "setup": "practice",
        "request_nonce": nonce,
    }
    response = http.post(
        activation_route.PATH,
        json={"action": "activate", "request_nonce": nonce},
        headers=headers(key=True),
    )
    assert response.status_code == 409 and response.json()["setup"] == "practice"
    assert snapshot(engine) == before


def test_ready_account_explicitly_activates_and_then_connects(engine, monkeypatch):
    args = case(engine)
    http, _ = client(engine, monkeypatch, args)
    before = snapshot(engine)
    response = http.post(
        context_route.PATH,
        json={"action": "connection", "request_nonce": str(uuid4())},
        headers=headers(),
    )
    assert response.status_code == 409 and response.json()["setup"] == "activation"
    assert snapshot(engine) == before
    nonce = str(uuid4())
    key_headers = headers(key=True)
    first = http.post(
        activation_route.PATH,
        json={"action": "activate", "request_nonce": nonce},
        headers=key_headers,
    )
    assert first.status_code == 200 and first.json()["state"] == "active"
    after = snapshot(engine)
    second = http.post(
        activation_route.PATH,
        json={"action": "activate", "request_nonce": nonce},
        headers=key_headers,
    )
    assert (
        second.status_code == 200
        and second.json() == first.json()
        and snapshot(engine) == after
    )
    response = http.post(
        context_route.PATH,
        json={"action": "connection", "request_nonce": str(uuid4())},
        headers=headers(),
    )
    assert response.status_code == 200 and response.json()["connection"][
        "practice_ref"
    ] == str(args["practice_id"])
    assert snapshot(engine) == after
    with session_for(engine) as session:
        audit = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref
                == response.json()["connection"]["provider_link_ref"]
            )
        ).one()
        assert any(ref["kind"] == "request" for ref in audit.platform_refs)


@pytest.mark.parametrize("change", ["baa", "entitlement"])
def test_incomplete_practice_requires_setup_before_activation(
    engine, monkeypatch, change
):
    args = case(engine)
    with session_for(engine) as session:
        practice = require_row(session, Practice, args["practice_id"])
        if change == "baa":
            practice.baa_accepted_at = None
        else:
            practice.billing_exempt = False
        session.commit()
    http, _ = client(engine, monkeypatch, args)
    before = snapshot(engine)
    for path, action, key in [
        (context_route.PATH, "connection", False),
        (activation_route.PATH, "activate", True),
    ]:
        response = http.post(
            path,
            json={"action": action, "request_nonce": str(uuid4())},
            headers=headers(key=key),
        )
        assert response.status_code == 409 and response.json()["setup"] == "practice"
    assert snapshot(engine) == before


def test_existing_grandfathered_connection_keeps_read_behavior(engine, monkeypatch):
    args = case(engine)
    activate(engine, args)
    with session_for(engine) as session:
        practice = require_row(session, Practice, args["practice_id"])
        doctor = require_row(session, Doctor, args["doctor_id"])
        grant(session, doctor, practice.id)
        practice.baa_accepted_at = None
        practice.billing_exempt = False
        session.commit()
    http, _ = client(engine, monkeypatch, args)
    before = snapshot(engine)
    response = http.post(
        context_route.PATH,
        json={"action": "connection", "request_nonce": str(uuid4())},
        headers=headers(),
    )
    assert response.status_code == 200
    assert snapshot(engine) == before


@pytest.mark.parametrize(
    "extra",
    [
        {"subject": "user_other"},
        {"tenant_id": str(uuid4())},
        {"practice_id": "practice_other"},
        {"confirmation": "CANCEL"},
    ],
)
def test_activation_body_cannot_choose_identity_or_tenant(engine, monkeypatch, extra):
    args = case(engine)
    http, _ = client(engine, monkeypatch, args)
    before = snapshot(engine)
    response = http.post(
        activation_route.PATH,
        json={"action": "activate", "request_nonce": str(uuid4()), **extra},
        headers=headers(key=True),
    )
    assert response.status_code == 422 and snapshot(engine) == before


@pytest.mark.parametrize(
    "bad_key", [None, "not-a-key", "00000000-0000-0000-0000-000000000000"]
)
def test_activation_requires_canonical_idempotency_key(engine, monkeypatch, bad_key):
    args = case(engine)
    http, _ = client(engine, monkeypatch, args)
    supplied = headers()
    if bad_key is not None:
        supplied["Idempotency-Key"] = bad_key
    before = snapshot(engine)
    response = http.post(
        activation_route.PATH,
        json={"action": "activate", "request_nonce": str(uuid4())},
        headers=supplied,
    )
    assert response.status_code == 422 and snapshot(engine) == before


def test_dark_route_never_activates(engine, monkeypatch):
    args = case(engine)
    http, _ = client(engine, monkeypatch, args, enabled=False)
    before = snapshot(engine)
    response = http.post(
        activation_route.PATH,
        json={"action": "activate", "request_nonce": str(uuid4())},
        headers=headers(key=True),
    )
    assert response.status_code == 503 and snapshot(engine) == before


def test_expired_activation_budget_creates_nothing(engine, monkeypatch):
    args = case(engine)
    _, settings = client(engine, monkeypatch, args)
    from app.services.companion_records import RecordDeadline

    before = snapshot(engine)
    with pytest.raises(RecordDeadline):
        scribe_native_activation.execute_activation(
            issuer=SETTINGS.clerk_issuer,
            subject=args["verified_clerk_subject"],
            session_id="sess_synthetic",
            request=ActivationRequest(action="activate", request_nonce=uuid4()),
            request_key=uuid4(),
            settings=settings,
            authority_settings=SETTINGS,
            deadline=RecordBudget(time.monotonic() - 1),
        )
    assert snapshot(engine) == before
