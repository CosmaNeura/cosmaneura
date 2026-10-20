"""Owned local action lifecycle; no external model, credentials or vendor calls."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.configuration.clerk import verified_provider_session
from app.services import billing_header_operations as prepared
from app.services import cab_bridge_authority as authority
from app.services.cab_bridge_contract import (
    CabActor,
    CabAuthorizationRequest,
    CabGenerationCompletion,
    digest,
)

from activemodel.session_manager import get_session
from app.models.billing_header_action import BillingHeaderAction as Action
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from tests.billing_header.operations_test import case as _case
from tests.billing_header.operations_test import command_for

case = _case


def intent(case):
    command = command_for(case)
    prepared.prepare_operation(case.actor, case.note_id, command)
    operation = UUID(command.operationId)
    actor = replace(case.actor, expires_at=datetime.now(UTC) + timedelta(seconds=50))
    body = {
        "encounterId": "synthetic-local",
        "expectedSource": {},
        "operationId": str(uuid4()),
        "responseMode": "async",
    }
    payload, action, replay = authority.begin_async_action(actor, operation, body)
    assert replay is False
    request = CabAuthorizationRequest(
        schemaVersion="core-cab-authorization-request.v1",
        purpose="header.generate",
        actor=CabActor(issuer=actor.issuer, subject=actor.subject),
        requestSha256=digest(payload),
        sourceContentSha256=command.expectedContentSha256,
        sourceTextSha256=command.expectedTextSha256,
    )
    return actor, operation, body, payload, action, request


def test_verified_signed_session_exp_is_normalized_without_token_storage():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    exp = int((datetime.now(UTC) + timedelta(seconds=55)).timestamp())
    payload = {
        "iss": "https://clerk.test.invalid",
        "sub": "user_synthetic",
        "sid": "synthetic-session",
        "exp": exp,
    }
    token = jwt.encode(payload, key, algorithm="RS256")
    verified = jwt.decode(
        token, key.public_key(), algorithms=["RS256"], issuer=payload["iss"]
    )
    actor = verified_provider_session(
        SimpleNamespace(is_signed_in=True, payload=verified)
    )
    assert actor.expires_at == datetime.fromtimestamp(exp, UTC)
    assert token not in repr(actor) and "synthetic-session" not in repr(actor)
    for bad in (True, "123", -1):
        with pytest.raises(ValueError):
            verified_provider_session(
                SimpleNamespace(is_signed_in=True, payload=payload | {"exp": bad})
            )


def test_async_original_deadline_and_exact_retry_cannot_renew(case):
    actor, operation, body, payload, action_id, request = intent(case)
    proof = authority.authorize_action(operation, request)
    expiry = datetime.fromisoformat(proof["generationExpiresAt"])
    assert expiry <= actor.expires_at
    assert datetime.fromisoformat(proof["expiresAt"]) <= expiry
    _, same, replay = authority.begin_async_action(
        replace(actor, expires_at=actor.expires_at + timedelta(hours=1)),
        operation,
        body,
    )
    assert replay and same == action_id
    assert (
        authority.authorize_action(operation, request)["generationExpiresAt"]
        == proof["generationExpiresAt"]
    )
    with pytest.raises(prepared.BillingPreparationError):
        authority.begin_async_action(
            actor, operation, body | {"expectedSource": {"changed": True}}
        )
    with get_session() as session:
        action = session.get(Action, action_id)
        assert action is not None
        assert action.request_sha256 == digest(payload)
        assert 0 < (action.expires_at - action.created_at).total_seconds() <= 50
    status_body = {k: v for k, v in body.items() if k != "responseMode"}
    status, status_id = authority.begin_action(
        actor, operation, "header.generation-status", status_body
    )
    status_request = request.model_copy(
        update={"purpose": "header.generation-status", "requestSha256": digest(status)}
    )
    assert "generationExpiresAt" not in authority.authorize_action(
        operation, status_request
    )
    authority.finish_action(status_id)
    assert (
        authority.authorize_action(operation, request)["generationExpiresAt"]
        == proof["generationExpiresAt"]
    )


def test_terminal_receipt_closes_once_and_never_reauthorizes(case):
    actor, operation, body, _, action_id, request = intent(case)
    receipt = CabGenerationCompletion(
        schemaVersion="core-cab-generation-completion.v1",
        **request.model_dump(exclude={"schemaVersion", "purpose"}),
        status="succeeded",
    )
    assert authority.complete_generation(operation, receipt)["closed"]
    assert authority.complete_generation(operation, receipt)["closed"]
    with pytest.raises(prepared.BillingPreparationError):
        authority.complete_generation(
            operation, receipt.model_copy(update={"status": "unknown"})
        )
    with pytest.raises(prepared.BillingPreparationError):
        authority.authorize_action(operation, request)
    assert authority.begin_async_action(actor, operation, body)[2] is True
    with get_session() as session:
        action = session.get(Action, action_id)
        assert action is not None
        database_id = action.id.uuid
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "UPDATE billing_header_action SET expires_at=expires_at+interval '1 second' WHERE id=:id"
                ),
                params={"id": database_id},
            )
            session.commit()
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "UPDATE billing_header_action SET completion_status='unknown' WHERE id=:id"
                ),
                params={"id": database_id},
            )
            session.commit()
        session.rollback()


def test_concurrent_claims_are_one_immutable_intent_and_completion_is_bounded(case):
    actor, operation, body, payload, action_id, request = intent(case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: authority.begin_async_action(actor, operation, body), range(2)
            )
        )
    assert all(value[1] == action_id and value[2] for value in results)
    receipt = CabGenerationCompletion(
        schemaVersion="core-cab-generation-completion.v1",
        **request.model_dump(exclude={"schemaVersion", "purpose"}),
        status="unknown",
    )

    def authorize():
        try:
            return authority.authorize_action(operation, request)["allowed"]
        except prepared.BillingPreparationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(authorize),
            pool.submit(authority.complete_generation, operation, receipt),
        ]
        assert tasks[0].result(timeout=5) in (True, False)
        assert tasks[1].result(timeout=5)["closed"]
    with get_session() as session:
        rows = session.exec(
            select(Action).where(
                Action.operation_id == operation,
                Action.generation_id == UUID(body["operationId"]),
            )
        ).all()
        assert len(rows) == 1 and rows[0].request_sha256 == digest(payload)


def test_expired_session_and_revoked_admission_deny_new_generation_and_status(
    case, monkeypatch
):
    actor, operation, body, _, _, request = intent(case)
    with pytest.raises(prepared.BillingPreparationError):
        authority.begin_async_action(
            replace(actor, expires_at=datetime.now(UTC) - timedelta(seconds=1)),
            operation,
            body,
        )
    with pytest.raises(prepared.BillingPreparationError):
        authority.begin_async_action(
            case.actor, operation, body | {"operationId": str(uuid4())}
        )
    from app.configuration import billing_header as config

    monkeypatch.setattr(config, "BILLING_SOURCE_POLICY", config.BillingSourcePolicy())
    with pytest.raises(prepared.BillingPreparationError):
        authority.authorize_action(operation, request)
    with pytest.raises(prepared.BillingPreparationError):
        authority.begin_action(
            actor,
            operation,
            "header.generation-status",
            {k: v for k, v in body.items() if k != "responseMode"},
        )


def test_shortened_current_admission_caps_lease_without_renewing_original(
    case, monkeypatch
):
    from app.configuration import billing_header as config

    _, operation, _, _, _, request = intent(case)
    original = authority.authorize_action(operation, request)["generationExpiresAt"]
    shortened = case.admission.model_copy(
        update={"expiresAt": (datetime.now(UTC) + timedelta(seconds=5)).isoformat()}
    )
    monkeypatch.setattr(
        config,
        "BILLING_SOURCE_POLICY",
        config.BILLING_SOURCE_POLICY.model_copy(update={"admissions": [shortened]}),
    )
    proof = authority.authorize_action(operation, request)
    assert datetime.fromisoformat(proof["expiresAt"]) <= datetime.fromisoformat(
        shortened.expiresAt
    )
    assert proof["generationExpiresAt"] == original
