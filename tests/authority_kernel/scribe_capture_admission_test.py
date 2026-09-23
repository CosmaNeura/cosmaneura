"""Admission recovery against the marked disposable PostgreSQL database.

These tests deliberately commit each phase, so another connection can revoke
authority during remote work. The downstream fake provides failure injection;
the real Scribe receiver is verified by a separate local wire rehearsal.
"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services import authority
from app.services import scribe_capture_admission as admission
from app.services.scribe_capture_scope import resolve_capture_binding
from app.services.scribe_workflow import (
    CaptureSession,
    ConsentRecord,
    DisabledScribeWorkflowClient,
    ScribeWorkflowRejectedError,
    ScribeWorkflowRetryableError,
)

from activemodel.session_manager import _session_context
from app.models.authority import AuthorityActorExternalIdentity
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from sqlmodel import Session, select

from tests.authority_kernel.scribe_capture_scope_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_capture_scope_test import (
    make_scope_case,
)

capture_engine = _capture_engine


class FakeScribe(DisabledScribeWorkflowClient):
    def __init__(self):
        self.consents = {}
        self.sessions = {}
        self.calls = []
        self.lose_response = set()
        self.after = None
        self.mutate = None
        self.entered = Event()
        self.release = Event()
        self.pause = False

    async def create_consent(self, tenant_id, payload, *, idempotency_key):
        self.calls.append(("consent", tenant_id, idempotency_key))
        if idempotency_key not in self.consents:
            self.consents[idempotency_key] = ConsentRecord(
                id=uuid4(),
                policy_version="all-party-v1",
                **payload.model_dump(),
            )
        receipt = self.consents[idempotency_key]
        if self.pause:
            self.entered.set()
            assert await asyncio.to_thread(self.release.wait, 10)
        return self._returned("consent", receipt)

    async def create_session(self, tenant_id, payload, *, idempotency_key):
        self.calls.append(("session", tenant_id, idempotency_key))
        consent = next(
            record
            for record in self.consents.values()
            if record.id == payload.consent_record_id
        )
        if consent.revoked_at is not None:
            raise ScribeWorkflowRejectedError("synthetic_remote_private_sentinel")
        if idempotency_key not in self.sessions:
            self.sessions[idempotency_key] = CaptureSession(
                id=uuid4(),
                status="created",
                created_at=datetime.now(UTC),
                **payload.model_dump(),
            )
        return self._returned("session", self.sessions[idempotency_key])

    def _returned(self, phase, receipt):
        if self.after:
            self.after(phase, receipt)
        if phase in self.lose_response:
            self.lose_response.remove(phase)
            raise ScribeWorkflowRetryableError("synthetic_remote_private_sentinel")
        if self.mutate:
            return self.mutate(phase, receipt)
        return receipt


@pytest.fixture
def admission_case(capture_engine):
    with Session(capture_engine, expire_on_commit=False) as session:
        context = _session_context.set(session)
        try:
            case = make_scope_case(session)
            binding = resolve_capture_binding(**case.arguments)
            caller = admission.CaptureCaller(
                **{
                    key: value
                    for key, value in case.arguments.items()
                    if key in admission.CaptureCaller.__dataclass_fields__
                }
            )
            attestation = admission.ConsentAttestation(
                modality="in_person",
                participant_regions=frozenset({"US-NY"}),
                method="verbal_attestation",
                all_parties_consented=True,
                granted_at=datetime.now(UTC),
            )
            yield SimpleNamespace(
                engine=capture_engine,
                session=session,
                case=case,
                caller=caller,
                binding=binding,
                attestation=attestation,
                client=FakeScribe(),
            )
        finally:
            _session_context.reset(context)


def call_arguments(fixture, *, resume=False):
    return {
        "caller": fixture.caller,
        "binding_ref": fixture.binding.binding_ref,
        "workspace_id": fixture.binding.workspace_id,
        "client_request_key": uuid4().hex,
        "attestation": None if resume else fixture.attestation,
        "client": fixture.client,
    }


def maybe_journal(fixture):
    with Session(fixture.engine) as session:
        return session.exec(
            select(ScribeCaptureAdmission).where(
                ScribeCaptureAdmission.binding_id == fixture.binding.binding_ref,
            )
        ).one_or_none()


def journal(fixture) -> ScribeCaptureAdmission:
    row = maybe_journal(fixture)
    assert row is not None
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("private sentinel"), "scribe_phase_timeout"),
        (
            ScribeWorkflowRetryableError("scribe_invalid_response"),
            "scribe_invalid_response",
        ),
        (ScribeWorkflowRejectedError("scribe_http_403"), "scribe_http_403"),
        (ScribeWorkflowRetryableError("private sentinel"), "scribe_remote_unconfirmed"),
        (ValueError("private sentinel"), "scribe_phase_unconfirmed"),
        (admission.AdmissionUnconfirmed(), "scribe_receipt_unconfirmed"),
    ],
)
async def test_failed_consent_keeps_safe_cause_and_same_recovery_key(
    admission_case, monkeypatch, error, expected
):
    original = admission_case.client.create_consent

    async def failed(*args, **kwargs):
        await original(*args, **kwargs)
        raise error

    monkeypatch.setattr(admission_case.client, "create_consent", failed)
    arguments = call_arguments(admission_case)
    with pytest.raises(admission.AdmissionUnconfirmed):
        await admission.admit_capture(**arguments)
    before = journal(admission_case)
    assert before.last_error_code == expected
    assert before.status == "consent_unconfirmed"
    assert before.consent_id is None
    assert len(admission_case.client.consents) == 1
    monkeypatch.setattr(admission_case.client, "create_consent", original)
    await admission.admit_capture(**{**arguments, "attestation": None})
    after = journal(admission_case)
    assert after.id == before.id
    assert after.consent_key == before.consent_key
    assert after.session_key == before.session_key
    assert after.last_error_code is None
    assert after.status == "ready"
    assert len(admission_case.client.consents) == 1


@pytest.mark.asyncio
async def test_committed_journal_precedes_remote_work_and_keys_survive_new_retry(
    admission_case,
):
    fixture = admission_case
    observed = []

    def before_return(phase, _receipt):
        row = journal(fixture)
        assert row is not None
        assert row.lease_phase == phase
        assert row.status == f"{phase}_unconfirmed"
        if phase == "session":
            assert row.consent_id is not None
        observed.append(phase)

    fixture.client.after = before_return
    first = await admission.admit_capture(**call_arguments(fixture))
    second = await admission.admit_capture(**call_arguments(fixture, resume=True))
    assert first.operation_ref == second.operation_ref
    assert first.session_id == second.session_id
    assert observed == ["consent", "session", "session"]
    assert len(fixture.client.consents) == len(fixture.client.sessions) == 1
    row = journal(fixture)
    assert row.status == "ready"
    assert row.lease_token is None
    assert fixture.caller.mount_context_token not in json.dumps(
        row.model_dump(mode="json")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["consent", "session"])
async def test_lost_response_reopens_in_fresh_database_session_with_same_remote_key(
    admission_case, phase
):
    fixture = admission_case
    fixture.client.lose_response.add(phase)
    with pytest.raises(admission.AdmissionUnconfirmed) as error:
        await admission.admit_capture(**call_arguments(fixture))
    assert "private_sentinel" not in str(error.value)
    row = journal(fixture)
    assert row.status == f"{phase}_unconfirmed"
    assert row.lease_token is None
    if phase == "session":
        assert row.consent_id is not None
    with Session(fixture.engine) as reopened:
        context = _session_context.set(reopened)
        try:
            recovered = await admission.admit_capture(
                **call_arguments(fixture, resume=True)
            )
        finally:
            _session_context.reset(context)
    attempts = [key for name, _tenant, key in fixture.client.calls if name == phase]
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert len(fixture.client.consents) == len(fixture.client.sessions) == 1
    assert recovered.session_id == journal(fixture).session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["consent", "session"])
async def test_authority_loss_during_remote_call_preserves_receipt_and_stops_disclosure(
    admission_case, phase
):
    fixture = admission_case

    def revoke(returned_phase, _receipt):
        if returned_phase != phase:
            return
        with Session(fixture.engine) as revoker:
            # If Core accidentally held its authority locks across the network
            # phase, this update would fail instead of completing immediately.
            from sqlalchemy import text

            revoker.execute(text("SET LOCAL lock_timeout = '1s'"))
            identity = revoker.get(
                AuthorityActorExternalIdentity, fixture.case.identity.id
            )
            assert identity is not None
            identity.revoked_at = datetime.now(UTC)
            revoker.add(identity)
            revoker.commit()

    fixture.client.after = revoke
    with pytest.raises(authority.AuthorityKernelError):
        await admission.admit_capture(**call_arguments(fixture))
    row = journal(fixture)
    assert row.consent_id is not None
    assert (row.session_id is not None) == (phase == "session")
    assert len(fixture.client.sessions) == (1 if phase == "session" else 0)


@pytest.mark.asyncio
async def test_changed_attestation_cannot_create_second_admission(admission_case):
    fixture = admission_case
    first = await admission.admit_capture(**call_arguments(fixture))
    calls = len(fixture.client.calls)
    changed = fixture.attestation.model_copy(
        update={"participant_regions": frozenset({"US-CA"})}
    )
    with pytest.raises(admission.AdmissionConflict):
        await admission.admit_capture(
            **{**call_arguments(fixture), "attestation": changed}
        )
    assert len(fixture.client.calls) == calls
    assert journal(fixture).session_id == first.session_id


@pytest.mark.asyncio
async def test_no_attestation_or_stale_attestation_cannot_start_new_admission(
    admission_case,
):
    fixture = admission_case
    for value in [
        None,
        fixture.attestation.model_copy(
            update={"granted_at": datetime.now(UTC) - timedelta(hours=1)}
        ),
    ]:
        with pytest.raises(admission.AdmissionConsentRequired):
            await admission.admit_capture(
                **{**call_arguments(fixture), "attestation": value}
            )
    assert maybe_journal(fixture) is None
    assert not fixture.client.calls


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_attestation_requires_an_explicit_true_boolean(value):
    with pytest.raises(ValidationError):
        admission.ConsentAttestation.model_validate(
            {
                "modality": "in_person",
                "participant_regions": ["US-NY"],
                "method": "verbal_attestation",
                "all_parties_consented": value,
                "granted_at": datetime.now(UTC),
            }
        )


@pytest.mark.asyncio
async def test_programmatic_model_copy_cannot_bypass_explicit_consent(admission_case):
    fixture = admission_case
    copied = fixture.attestation.model_copy(update={"all_parties_consented": False})
    with pytest.raises(admission.AdmissionConsentRequired):
        await admission.admit_capture(
            **{**call_arguments(fixture), "attestation": copied}
        )
    assert maybe_journal(fixture) is None
    assert not fixture.client.calls


@pytest.mark.asyncio
async def test_ready_receipt_does_not_bypass_current_scribe_consent(admission_case):
    fixture = admission_case
    first = await admission.admit_capture(**call_arguments(fixture))
    key, record = next(iter(fixture.client.consents.items()))
    fixture.client.consents[key] = record.model_copy(
        update={"revoked_at": datetime.now(UTC)}
    )
    with pytest.raises(admission.AdmissionUnconfirmed):
        await admission.admit_capture(**call_arguments(fixture, resume=True))
    assert journal(fixture).session_id == first.session_id
    assert len(fixture.client.sessions) == 1


@pytest.mark.asyncio
async def test_revoked_consent_recovered_after_lost_response_is_retained_and_denied(
    admission_case,
):
    fixture = admission_case
    fixture.client.lose_response.add("consent")
    with pytest.raises(admission.AdmissionUnconfirmed):
        await admission.admit_capture(**call_arguments(fixture))
    key, record = next(iter(fixture.client.consents.items()))
    fixture.client.consents[key] = record.model_copy(
        update={"revoked_at": datetime.now(UTC)}
    )
    with pytest.raises(admission.AdmissionConsentUnavailable):
        await admission.admit_capture(**call_arguments(fixture, resume=True))
    row = journal(fixture)
    assert row.status == "consent_unavailable" and row.consent_id == record.id
    assert row.consent_receipt is not None
    assert row.consent_receipt["revoked_at"] is not None
    assert not fixture.client.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["consent", "session"])
async def test_mismatched_remote_receipt_never_attaches_to_encounter(
    admission_case, phase
):
    fixture = admission_case
    fixture.client.mutate = lambda current, receipt: (
        receipt.model_copy(update={"patient_id": "patient_synthetic_foreign_sentinel"})
        if current == phase
        else receipt
    )
    with pytest.raises(admission.AdmissionUnconfirmed):
        await admission.admit_capture(**call_arguments(fixture))
    row = journal(fixture)
    assert getattr(row, phase + "_receipt") is None
    assert "foreign_sentinel" not in json.dumps(row.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_expired_lease_after_remote_acceptance_recovers_same_session(
    admission_case,
):
    fixture = admission_case
    reference = admission._prepare(
        fixture.caller,
        fixture.binding.binding_ref,
        fixture.binding.workspace_id,
        uuid4().hex,
        fixture.attestation,
    )
    claim = admission._claim(
        fixture.caller,
        fixture.binding.binding_ref,
        fixture.binding.workspace_id,
        reference,
    )
    await fixture.client.create_consent(
        claim.scribe_tenant_id, claim.payload, idempotency_key=claim.key
    )
    # Discard the successful remote response, as if the process exited before
    # its local receipt write. Only lease expiry permits a new claimant.
    with pytest.raises(admission.AdmissionInProgress):
        await admission.admit_capture(**call_arguments(fixture, resume=True))
    with Session(fixture.engine) as repair:
        row = repair.get(ScribeCaptureAdmission, reference)
        assert row is not None
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        repair.add(row)
        repair.commit()
    recovered = await admission.admit_capture(**call_arguments(fixture, resume=True))
    assert (
        recovered.consent_record_id == next(iter(fixture.client.consents.values())).id
    )
    assert len(fixture.client.consents) == len(fixture.client.sessions) == 1


def test_simultaneous_admission_uses_one_durable_lease(admission_case):
    fixture = admission_case
    fixture.client.pause = True

    def first_request():
        with Session(fixture.engine) as session:
            context = _session_context.set(session)
            try:
                return asyncio.run(admission.admit_capture(**call_arguments(fixture)))
            finally:
                _session_context.reset(context)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first_request)
        try:
            assert fixture.client.entered.wait(10)
            with pytest.raises(admission.AdmissionInProgress):
                asyncio.run(
                    admission.admit_capture(**call_arguments(fixture, resume=True))
                )
        finally:
            fixture.client.release.set()
        result = pending.result(timeout=15)
    assert result.session_id == journal(fixture).session_id
    assert len(fixture.client.consents) == len(fixture.client.sessions) == 1


@pytest.mark.asyncio
async def test_new_signed_in_context_resumes_same_creator_session(admission_case):
    fixture = admission_case
    first = await admission.admit_capture(**call_arguments(fixture))
    case = fixture.case
    case.mount.status = "revoked"
    fixture.session.add(case.mount)
    fixture.session.commit()
    with pytest.raises(authority.AuthorityKernelError):
        await admission.admit_capture(**call_arguments(fixture, resume=True))
    issued = authority.exchange_context_token(
        clerk_issuer=case.identity.issuer,
        clerk_subject=case.identity.subject,
        requested_surface="provider_assistant",
        record_ref=str(case.appointment.id),
        session=fixture.session,
        settings=fixture.caller.settings,
    )
    turn = uuid4()
    resolved = authority.resolve_mount_context(
        workload_client_id=fixture.caller.workload_client_id,
        mount_context_token=issued.context_token,
        client_turn_id=turn,
        session=fixture.session,
        settings=fixture.caller.settings,
    )
    fresh_caller = admission.CaptureCaller(
        workload_client_id=fixture.caller.workload_client_id,
        mount_context_token=issued.context_token,
        interaction_ref=resolved.interaction_ref,
        client_turn_id=turn,
        settings=fixture.caller.settings,
        capture_settings=fixture.caller.capture_settings,
    )
    second = await admission.admit_capture(
        **{**call_arguments(fixture, resume=True), "caller": fresh_caller}
    )
    assert second.operation_ref == first.operation_ref
    assert second.session_id == first.session_id


@pytest.mark.asyncio
async def test_late_receipt_preserves_another_claimants_lease(admission_case):
    fixture = admission_case
    reference = admission._prepare(
        fixture.caller,
        fixture.binding.binding_ref,
        fixture.binding.workspace_id,
        uuid4().hex,
        fixture.attestation,
    )
    first = admission._claim(
        fixture.caller,
        fixture.binding.binding_ref,
        fixture.binding.workspace_id,
        reference,
    )
    receipt = await fixture.client.create_consent(
        first.scribe_tenant_id, first.payload, idempotency_key=first.key
    )
    with Session(fixture.engine) as repair:
        row = repair.get(ScribeCaptureAdmission, reference)
        assert row is not None
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        repair.add(row)
        repair.commit()
    second = admission._claim(
        fixture.caller,
        fixture.binding.binding_ref,
        fixture.binding.workspace_id,
        reference,
    )
    assert first.key == second.key
    admission._accept_receipt(first, receipt)
    assert journal(fixture).lease_token == second.lease_token
    admission._accept_receipt(second, receipt)
    assert journal(fixture).lease_token is None
    result = await admission.admit_capture(**call_arguments(fixture, resume=True))
    assert result.consent_record_id == receipt.id
    assert len(fixture.client.sessions) == 1


@pytest.mark.asyncio
async def test_cancelled_request_retains_lease_and_stable_recovery(admission_case):
    fixture = admission_case
    fixture.client.pause = True
    pending = asyncio.create_task(admission.admit_capture(**call_arguments(fixture)))
    try:
        assert await asyncio.to_thread(fixture.client.entered.wait, 10)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        fixture.client.release.set()
    row = journal(fixture)
    assert row.lease_token is not None
    assert row.status == "consent_unconfirmed"
    with Session(fixture.engine) as repair:
        saved = repair.get(ScribeCaptureAdmission, row.id)
        assert saved is not None
        saved.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        repair.add(saved)
        repair.commit()
    fixture.client.pause = False
    await admission.admit_capture(**call_arguments(fixture, resume=True))
    assert len(fixture.client.consents) == len(fixture.client.sessions) == 1
