"""Admission validates an existing mounted binding without resolving it again."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from typeid import TypeID

from app.services import authority, scribe_encounters
from app.services import scribe_capture_admission as admission
from app.services import scribe_native_capture as service

from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlalchemy import event
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import select

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

engine, native, workflow = _engine, _native, _workflow


def mounted_binding(native):
    response = native.post()
    assert response.status_code == 200
    return response.json()["result"]["binding_ref"]


def prepare(native, binding):
    return native.post(
        action="preparation_context",
        preparation="admission",
        binding_ref=binding,
        opening_nonce=None,
    )


def test_admission_uses_three_fresh_lookups_and_never_creates_binding(
    native, engine, monkeypatch, record_property
):
    binding = mounted_binding(native)
    lookups, phases, capture_sql = [], [], []
    original = scribe_encounters.encounter_workflow

    def lookup(**kwargs):
        lookups.append(kwargs["expected_capture_binding_ref"])
        return original(**kwargs)

    def forbidden(**_):
        pytest.fail("Admission must not resolve/create its mounted binding again")

    def observe(_connection, _cursor, sql, *_):
        shape = " ".join(sql.lower().split())
        if "scribe_capture_binding" in shape:
            capture_sql.append(shape)

    from contextlib import contextmanager

    original_phase = service.phase

    @contextmanager
    def phase(name):
        phases.append(name)
        with original_phase(name):
            yield

    monkeypatch.setattr(service, "phase", phase)
    monkeypatch.setattr(scribe_encounters, "encounter_workflow", lookup)
    monkeypatch.setattr(
        service.scribe_capture_scope, "resolve_capture_binding", forbidden
    )
    event.listen(engine, "before_cursor_execute", observe)
    try:
        response = prepare(native, binding)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert response.status_code == 200
    assert lookups == [binding] * 3
    assert "policy_binding" not in phases
    assert len(capture_sql) == 3 and all(
        sql.startswith("select ") for sql in capture_sql
    )
    result = response.json()["result"]
    assert result["binding_ref"] == binding
    assert result["appointment_ref"] == native.row["appointment_ref"]
    assert engine.pool.checkedout() == 0
    with session_for(engine) as session:
        assert (
            len(
                session.exec(
                    select(ScribeCaptureBinding).where(
                        ScribeCaptureBinding.workspace_id == native.row["workspace_id"]
                    )
                ).all()
            )
            == 1
        )
    record_property("binding_resolution_transactions", "1 -> 0")
    record_property("fresh_lookup_transactions", "4 -> 3")


@pytest.mark.parametrize("wrong", ["missing", "identifier"])
def test_absent_or_wrong_existing_binding_denies_without_recreation(
    native, engine, wrong
):
    binding = mounted_binding(native)
    if wrong == "missing":
        created = native.invoke(native.request())
        response = native.post(
            action="preparation_context",
            preparation="admission",
            binding_ref=binding,
            workspace_id=created["encounter"]["workspace_id"],
            opening_nonce=None,
        )
        assert response.status_code == 403
        with session_for(engine) as session:
            assert (
                session.exec(
                    select(ScribeCaptureBinding).where(
                        ScribeCaptureBinding.workspace_id
                        == created["encounter"]["workspace_id"]
                    )
                ).one_or_none()
                is None
            )
    else:
        binding = str(TypeID("scribe_capture_binding"))
        assert prepare(native, binding).status_code == 403
    assert engine.pool.checkedout() == 0


FIELDS = [
    "provider_link_id",
    "actor_id",
    "tenant_id",
    "source_identity_id",
    "person_binding_id",
    "doctor_id",
    "patient_id",
    "appointment_id",
    "practice_id",
    "scribe_tenant_id",
    "scope_sha256",
    "revoked_at",
]


def corrupt(row, field):
    if field == "scope_sha256":
        value = "0" * 64
    elif field == "revoked_at":
        value = datetime.now(UTC)
    elif field == "scribe_tenant_id":
        value = uuid4()
    else:
        value = TypeID(getattr(row, field).prefix)
    set_committed_value(row, field, value)


@pytest.mark.parametrize("field", FIELDS)
def test_every_current_binding_scope_input_is_checked(
    native, engine, monkeypatch, field
):
    binding = mounted_binding(native)
    original = scribe_encounters._visit

    def changed(session, row, **kwargs):
        current = session.get(ScribeCaptureBinding, binding)
        corrupt(current, field)
        # Simulate the newly fetched current row, with no DB write or foreign
        # key tampering. The guard must check every input and current digest.
        original_exec = session.exec

        def read(statement, *args, **kw):
            if "FROM scribe_capture_binding" in str(statement):
                from types import SimpleNamespace

                return SimpleNamespace(one_or_none=lambda: current)
            return original_exec(statement, *args, **kw)

        with monkeypatch.context() as patch:
            patch.setattr(session, "exec", read)
            return original(session, row, **kwargs)

    monkeypatch.setattr(scribe_encounters, "_visit", changed)
    assert prepare(native, binding).status_code == 403
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("field", ["scope_sha256", "revoked_at", "scribe_tenant_id"])
@pytest.mark.parametrize("boundary", ["exchange", "resolve"])
def test_committed_binding_change_after_service_boundary_denies(
    native, engine, monkeypatch, field, boundary
):
    binding = mounted_binding(native)
    name = (
        "exchange_context_token" if boundary == "exchange" else "resolve_mount_context"
    )
    original = getattr(authority, name)

    def changed(**kwargs):
        result = original(**kwargs)
        if kwargs.get("session") is not None:
            # resolve_mount_context recursively enters its managed session.
            # Only the outer call is the completed service boundary.
            return result
        assert engine.pool.checkedout() == 0
        with session_for(engine) as writer:
            current = writer.get(ScribeCaptureBinding, binding)
            setattr(
                current,
                field,
                datetime.now(UTC)
                if field == "revoked_at"
                else "0" * 64
                if field == "scope_sha256"
                else uuid4(),
            )
            writer.add(current)
            writer.commit()
        return result

    monkeypatch.setattr(authority, name, changed)
    assert prepare(native, binding).status_code == 403
    assert engine.pool.checkedout() == 0


def test_refresh_ignores_stale_loaded_capture_scope(native, engine, monkeypatch):
    binding = mounted_binding(native)
    original = scribe_encounters._visit

    def stale(session, row, **kwargs):
        current = session.get(ScribeCaptureBinding, binding)
        set_committed_value(current, "scope_sha256", "0" * 64)
        result = original(session, row, **kwargs)
        assert current.scope_sha256 != "0" * 64
        return result

    monkeypatch.setattr(scribe_encounters, "_visit", stale)
    assert prepare(native, binding).status_code == 200


def test_public_native_request_cannot_supply_internal_binding_lookup_option(native):
    assert (
        native.post(expected_capture_binding_ref=mounted_binding(native)).status_code
        == 422
    )


@pytest.mark.parametrize("field", ["scope_sha256", "revoked_at", "scribe_tenant_id"])
def test_change_after_preparation_denies_before_durable_admission_remote_work(
    native, engine, monkeypatch, field
):
    import asyncio
    from uuid import UUID

    from app.configuration.scribe_capture import ScribeCaptureSettings
    from app.services.scribe_capture_deadline import CaptureBudget, capture_budget
    from app.services.scribe_configured_capture_contract import (
        ConfiguredCaptureSelection,
    )

    from tests.authority_kernel.companion_provider_provisioning_test import SETTINGS

    binding = mounted_binding(native)
    prepared = prepare(native, binding)
    assert prepared.status_code == 200
    result = prepared.json()["result"]
    caller = admission.CaptureCaller(
        workload_client_id="11111111-1111-4111-8111-111111111111",
        mount_context_token=result["context_token"],
        interaction_ref=result["interaction_ref"],
        client_turn_id=UUID(result["client_turn_id"]),
        settings=SETTINGS,
        capture_settings=ScribeCaptureSettings(enabled=True),
    )
    with session_for(engine) as writer:
        current = writer.get(ScribeCaptureBinding, binding)
        setattr(
            current,
            field,
            datetime.now(UTC)
            if field == "revoked_at"
            else "0" * 64
            if field == "scope_sha256"
            else uuid4(),
        )
        writer.add(current)
        writer.commit()

    async def forbidden(*_, **__):
        pytest.fail("Changed binding must deny before Scribe admission I/O")

    for name in ["create_consent", "create_session", "create_configured_session"]:
        monkeypatch.setattr(native.remote, name, forbidden)

    async def invoke():
        with capture_budget(CaptureBudget(20)):
            return await admission.admit_capture(
                caller=caller,
                binding_ref=binding,
                workspace_id=UUID(native.row["workspace_id"]),
                client_request_key="changed-after-preparation",
                attestation=admission.ConsentAttestation(
                    modality="in_person",
                    participant_regions=frozenset({"US-NY"}),
                    method="verbal_attestation",
                    all_parties_consented=True,
                    granted_at=datetime.now(UTC),
                ),
                configured_capture=ConfiguredCaptureSelection(
                    schema_version="1.0", language_mode="en-US"
                ),
                client=native.remote,
            )

    with pytest.raises(authority.AuthorityKernelError):
        asyncio.run(invoke())
    assert engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_disabled_capture_setting_denies_admission_context_before_lookup(
    monkeypatch,
):
    from app.configuration.scribe_capture import ScribeCaptureSettings

    from tests.authority_kernel.scribe_native_capture_service_test import fixture

    original, calls, _, invoke = fixture(monkeypatch)
    data = service.NativeCaptureRequest(
        action="preparation_context",
        preparation="admission",
        workspace_id=original.workspace_id,
        request_nonce=uuid4(),
        expected_actor_ref=original.expected_actor_ref,
        expected_tenant_ref=original.expected_tenant_ref,
        binding_ref=str(TypeID("scribe_capture_binding")),
    )
    original_call = service.execute_native_capture

    async def disabled(**kwargs):
        return await original_call(
            **(kwargs | {"capture_settings": ScribeCaptureSettings(enabled=False)})
        )

    monkeypatch.setattr(service, "execute_native_capture", disabled)
    with pytest.raises(authority.AuthorityKernelDisabledError):
        await invoke(data)
    assert calls == []
