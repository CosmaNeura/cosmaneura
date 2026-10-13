"""Current native identity/policy plus existing durable window checks on real SQL."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.services import scribe_native_capture as service

from app.models.authority import AuthorityActorExternalIdentity, MountContextToken
from app.models.scribe_capture_binding import (
    ScribeCaptureBinding,
    ScribeCaptureProviderLink,
)
from sqlmodel import Session, select

from tests.authority_kernel.scribe_native_capture_receiver_test import (
    PATH,
    body,
    client,
    headers,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)
from tests.authority_kernel.scribe_recording_window_test import operation
from tests.authority_kernel.scribe_window_scope_fence_test import install_remote

engine, native, workflow = _engine, _native, _workflow


def prepared(native):
    response = native.post(
        "creator_review",
        action="preparation_context",
        operation=None,
        preparation="admission",
    )
    assert response.status_code == 200
    value = response.json()["result"]
    return {k: value[k] for k in ("context_token", "interaction_ref", "client_turn_id")}


def window(native, context, action="prepared_window"):
    return native.post(
        "native_recording_preparation"
        if action == "prepared_native_read"
        else "creator_review",
        action=action,
        operation={"kind": "native_recording_preparation"}
        if action == "prepared_native_read"
        else operation().model_dump(mode="json"),
        prepared_context=context,
    )


def test_real_prepared_window_reuses_context_and_retains_original_keys(
    native, engine, monkeypatch
):
    context = prepared(native)
    calls = install_remote(native, monkeypatch)
    response = window(native, context)
    assert response.status_code == 200
    assert [row[0] for row in calls] == ["prepare_recording_window"]
    assert response.json()["result"]["operation"] == "prepare_recording_window"
    assert context["context_token"] not in response.text
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "fault",
    [
        "token_expired",
        "token_revoked",
        "context_old",
        "future_context",
        "identity",
        "provider_link",
        "binding",
        "turn",
        "interaction",
        "foreign_workspace",
    ],
)
@pytest.mark.parametrize("action", ["prepared_window", "prepared_native_read"])
def test_committed_revocation_and_misbound_context_prevent_remote_mutation(
    native, engine, monkeypatch, fault, action
):
    context = prepared(native)
    calls = install_remote(native, monkeypatch)
    if fault in {"turn", "interaction"}:
        context["client_turn_id" if fault == "turn" else "interaction_ref"] = str(
            uuid4()
        )
    elif fault == "foreign_workspace":
        response = native.post(
            "creator_review",
            action=action,
            operation={"kind": "native_recording_preparation"}
            if action == "prepared_native_read"
            else operation().model_dump(mode="json"),
            prepared_context=context,
            workspace_id=str(uuid4()),
        )
        assert response.status_code == 403 and not calls
        return
    else:
        with Session(engine) as session:
            if fault in {
                "token_expired",
                "token_revoked",
                "context_old",
                "future_context",
            }:
                row = session.exec(
                    select(MountContextToken).where(
                        MountContextToken.token_hash
                        == hashlib.sha256(context["context_token"].encode()).hexdigest()
                    )
                ).one()
                field = (
                    "expires_at"
                    if fault == "token_expired"
                    else "revoked_at"
                    if fault == "token_revoked"
                    else "issued_at"
                )
                value = datetime.now(UTC) + (
                    timedelta(seconds=1)
                    if fault == "future_context"
                    else timedelta(seconds=-31)
                    if fault == "context_old"
                    else timedelta(seconds=-1)
                )
            else:
                model = {
                    "identity": AuthorityActorExternalIdentity,
                    "provider_link": ScribeCaptureProviderLink,
                    "binding": ScribeCaptureBinding,
                }[fault]
                query = select(model)
                if model is AuthorityActorExternalIdentity:
                    query = query.where(
                        model.subject == native.args["verified_clerk_subject"]
                    )
                if model is ScribeCaptureProviderLink:
                    query = query.where(
                        model.id == native.provisioned.provider_link_ref
                    )
                elif model is ScribeCaptureBinding:
                    query = query.where(
                        model.workspace_id == UUID(native.row["workspace_id"])
                    )
                row = session.exec(query).one()
                field, value = "revoked_at", datetime.now(UTC)
            setattr(row, field, value)
            session.add(row)
            session.commit()
    response = window(native, context, action)
    assert (
        response.status_code in (403, 409)
        and not calls
        and "receipt" not in response.text
    )
    assert engine.pool.checkedout() == 0


def test_revocation_during_remote_window_blocks_ready_publication(
    native, engine, monkeypatch
):
    context = prepared(native)

    def revoke(_kind):
        with Session(engine) as session:
            row = session.exec(
                select(AuthorityActorExternalIdentity).where(
                    AuthorityActorExternalIdentity.subject
                    == native.args["verified_clerk_subject"]
                )
            ).one()
            row.revoked_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    calls = install_remote(native, monkeypatch, after=revoke)
    response = window(native, context)
    assert (
        response.status_code in (403, 409)
        and len(calls) == 1
        and "receipt" not in response.text
    )
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("denied", ["agent", "capture", "identity", "disabled"])
@pytest.mark.parametrize(
    "action", ["prepared_window", "prepared_admission", "prepared_native_read"]
)
def test_prepared_context_retains_native_route_policies_before_dispatch(
    monkeypatch, denied, action
):
    from fastapi import HTTPException

    from app.routes import scribe_native_capture as route

    connection, calls = client(monkeypatch, enabled=denied != "disabled")

    def reject(*_args, **_kwargs):
        raise HTTPException(403, "denied")

    if denied in {"agent", "capture"}:
        monkeypatch.setattr(
            route,
            "authenticate_" + denied + "_api_request"
            if denied == "agent"
            else "authenticate_scribe_capture_request",
            reject,
        )
    if denied == "identity":

        def reject_identity():
            raise HTTPException(403, "denied")

        connection.app.dependency_overrides[
            route.authenticate_embed_platform_session
        ] = reject_identity
    response = connection.post(
        PATH,
        json=body(
            action=action,
            binding_ref="scribe_capture_binding_" + "0" * 26,
            operation=operation().model_dump(mode="json"),
            prepared_context={
                "context_token": "invented-server-context",
                "interaction_ref": "invented-interaction-012345",
                "client_turn_id": str(uuid4()),
            },
        ),
        headers=headers(),
    )
    assert response.status_code in (403, 503)
    assert not any(isinstance(c, dict) for c in calls)


@pytest.mark.parametrize(
    "kind",
    [
        "source_start_authorize",
        "bind_key",
        "issue_lease",
        "reserve_chunks",
        "complete",
        "creator_review",
    ],
)
def test_prepared_context_cannot_enable_other_operations(kind):
    values = body(
        action="prepared_window",
        binding_ref="scribe_capture_binding_" + "0" * 26,
        operation={"kind": kind},
        prepared_context={
            "context_token": "invented-server-context",
            "interaction_ref": "invented-interaction-012345",
            "client_turn_id": str(uuid4()),
        },
    )
    with pytest.raises(ValidationError):
        service.NativeCaptureRequest.model_validate(values)


@pytest.mark.parametrize(
    "kind",
    [
        "creator_review",
        "source_start_authorize",
        "bind_key",
        "issue_lease",
        "reserve_chunks",
        "complete",
        "prepare_recording_window",
    ],
)
def test_prepared_native_read_is_limited_to_native_preparation(kind):
    values = body(
        action="prepared_native_read",
        binding_ref="scribe_capture_binding_" + "0" * 26,
        operation={"kind": kind},
        prepared_context={
            "context_token": "invented-server-context",
            "interaction_ref": "invented-interaction-012345",
            "client_turn_id": str(uuid4()),
        },
    )
    with pytest.raises(ValidationError):
        service.NativeCaptureRequest.model_validate(values)


@pytest.mark.parametrize("fault", ["actor", "tenant", "appointment", "patient"])
def test_foreign_context_cannot_mutate_the_current_selected_visit(
    native, engine, monkeypatch, fault
):
    context = prepared(native)
    calls = install_remote(native, monkeypatch)
    original = service.scribe_encounters.encounter_workflow

    def altered(**kwargs):
        value = original(**kwargs)
        if fault in {"appointment", "patient"}:
            value["encounter"][fault + "_ref"] = fault + "_00000000000000000000000001"
        return value

    if fault in {"appointment", "patient"}:
        monkeypatch.setattr(service.scribe_encounters, "encounter_workflow", altered)
    changes = (
        {
            "expected_" + fault + "_ref": "authority_"
            + fault
            + "_00000000000000000000000001"
        }
        if fault in {"actor", "tenant"}
        else {}
    )
    response = native.post(
        "creator_review",
        action="prepared_window",
        operation=operation().model_dump(mode="json"),
        prepared_context=context,
        **changes,
    )
    assert response.status_code in (403, 409) and not calls


def test_context_expiry_while_remote_mutation_runs_blocks_ready(
    native, engine, monkeypatch
):
    context = prepared(native)

    def expire(_kind):
        with Session(engine) as session:
            row = session.exec(
                select(MountContextToken).where(
                    MountContextToken.token_hash
                    == hashlib.sha256(context["context_token"].encode()).hexdigest()
                )
            ).one()
            row.issued_at = datetime.now(UTC) - timedelta(seconds=31)
            session.add(row)
            session.commit()

    calls = install_remote(native, monkeypatch, after=expire)
    response = window(native, context)
    assert (
        response.status_code == 403
        and len(calls) == 1
        and "receipt" not in response.text
    )


def new_mounted_context(native):
    created = native.invoke(native.request())
    response = native.post(
        "creator_review",
        action="mount",
        operation=None,
        binding_ref=None,
        opening_nonce=None,
        workspace_id=created["encounter"]["workspace_id"],
        retain_preparation_context=True,
    )
    assert response.status_code == 200
    return response.json()["result"]


def prepared_admission(native, mounted, **changes):
    return native.post(
        "creator_review",
        action="prepared_admission",
        operation=None,
        workspace_id=mounted["workspace_id"],
        binding_ref=mounted["binding_ref"],
        prepared_context=mounted["prepared_context"],
        attestation={
            "modality": "in_person",
            "participant_regions": ["US-NY"],
            "method": "verbal_attestation",
            "all_parties_consented": True,
            "granted_at": datetime.now(UTC).isoformat(),
        },
        configured_capture={"schema_version": "1.0", "language_mode": "en-US"},
        **changes,
    )


def test_mount_context_runs_real_consent_and_configured_admission(native, engine):
    mounted = new_mounted_context(native)
    token = mounted["prepared_context"]["context_token"]
    before = len(native.remote.consents), len(native.remote.sessions)
    answer = prepared_admission(native, mounted)
    assert answer.status_code == 200, answer.text
    result = answer.json()["result"]
    assert (
        result["status"] == "admitted"
        and result["workspace_id"] == mounted["workspace_id"]
    )
    assert result["configured_capture"] == {
        "schema_version": "1.0",
        "language_mode": "en-US",
    }
    assert (len(native.remote.consents), len(native.remote.sessions)) == (
        before[0] + 1,
        before[1] + 1,
    )
    assert token not in answer.text and "prepared_context" not in result
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "fault", ["expired", "revoked", "provider_link", "identity", "wrong_turn"]
)
def test_prepared_admission_still_denies_committed_authority_changes(
    native, engine, fault
):
    mounted = new_mounted_context(native)
    before = len(native.remote.consents), len(native.remote.sessions)
    if fault == "wrong_turn":
        mounted["prepared_context"]["client_turn_id"] = str(uuid4())
    else:
        with Session(engine) as session:
            if fault in {"expired", "revoked"}:
                token = session.exec(
                    select(MountContextToken).where(
                        MountContextToken.token_hash
                        == hashlib.sha256(
                            mounted["prepared_context"]["context_token"].encode()
                        ).hexdigest()
                    )
                ).one()
                if fault == "expired":
                    token.issued_at = datetime.now(UTC) - timedelta(seconds=31)
                else:
                    token.revoked_at = datetime.now(UTC)
                session.add(token)
            elif fault == "provider_link":
                link = session.get(
                    ScribeCaptureProviderLink, native.provisioned.provider_link_ref
                )
                link.revoked_at = datetime.now(UTC)
                session.add(link)
            else:
                identity = session.exec(
                    select(AuthorityActorExternalIdentity).where(
                        AuthorityActorExternalIdentity.subject
                        == native.args["verified_clerk_subject"]
                    )
                ).one()
                identity.revoked_at = datetime.now(UTC)
                session.add(identity)
            session.commit()
    response = prepared_admission(native, mounted)
    assert response.status_code == 403
    assert (len(native.remote.consents), len(native.remote.sessions)) == before


def test_private_mount_context_is_opt_in_and_cannot_enable_other_actions(native):
    ordinary = native.post()
    assert (
        ordinary.status_code == 200
        and "prepared_context" not in ordinary.json()["result"]
    )
    assert (
        native.post("creator_review", retain_preparation_context=True).status_code
        == 422
    )
    mounted = new_mounted_context(native)
    bad = dict(mounted)
    bad["prepared_context"] = dict(mounted["prepared_context"], extra="untrusted")
    assert prepared_admission(native, bad).status_code == 422


def test_prepared_admission_revocation_during_remote_creation_blocks_publication(
    native, engine, monkeypatch
):
    mounted = new_mounted_context(native)
    original = native.remote.create_configured_session

    async def revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        with Session(engine) as session:
            row = session.exec(
                select(AuthorityActorExternalIdentity).where(
                    AuthorityActorExternalIdentity.subject
                    == native.args["verified_clerk_subject"]
                )
            ).one()
            row.revoked_at = datetime.now(UTC)
            session.add(row)
            session.commit()
        return result

    monkeypatch.setattr(native.remote, "create_configured_session", revoke)
    response = prepared_admission(native, mounted)
    assert response.status_code == 403 and "session_id" not in response.json()
    assert engine.pool.checkedout() == 0
