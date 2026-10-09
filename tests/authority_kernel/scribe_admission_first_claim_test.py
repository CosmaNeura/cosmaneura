"""Actual journal, policy, leases and cancellation on owned synthetic PG."""

from collections import Counter
from datetime import UTC, datetime

import pytest

from app.services import authority
from app.services import scribe_capture_admission as admission
from app.services.scribe_capture_deadline import (
    CaptureBudget,
    CaptureDeadlineExceeded,
    capture_budget,
)
from app.services.scribe_configured_capture_contract import ConfiguredCaptureSelection

from sqlalchemy import event

from tests.authority_kernel.scribe_capture_admission_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_admission_test import (
    call_arguments,
    journal,
    maybe_journal,
)
from tests.authority_kernel.scribe_capture_admission_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_configured_capture_test import install_configured

admission_case, capture_engine = _admission_case, _capture_engine


def first_claim(fixture, **changes):
    arguments = call_arguments(fixture) | changes
    arguments.pop("client")
    return admission._prepare_and_claim(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("configured", [False, True])
async def test_first_dispatch_has_committed_lease_without_open_transaction(
    admission_case, monkeypatch, resume, configured
):
    fixture = admission_case
    selection = (
        ConfiguredCaptureSelection(schema_version="1.0", language_mode="en-US")
        if configured
        else None
    )
    if configured:
        install_configured(fixture)
    if resume:
        await admission.admit_capture(
            **call_arguments(fixture), configured_capture=selection
        )
    original_authorize = admission.CaptureCaller.authorize
    authorizations = []

    def authorize(self, *args):
        authorizations.append(True)
        return original_authorize(self, *args)

    monkeypatch.setattr(admission.CaptureCaller, "authorize", authorize)
    seen = []
    original_consent = fixture.client.create_consent
    session_method = "create_configured_session" if configured else "create_session"
    original_session = getattr(fixture.client, session_method)

    async def remote(original, phase, *args, **kwargs):
        assert not fixture.session.in_transaction()
        persisted = journal(fixture)
        assert persisted.lease_token is not None
        assert persisted.lease_phase == phase
        assert persisted.lease_expires_at > datetime.now(UTC)
        assert len(authorizations) == (1 if not seen else 2)
        seen.append(phase)
        return await original(*args, **kwargs)

    monkeypatch.setattr(
        fixture.client,
        "create_consent",
        lambda *a, **k: remote(original_consent, "consent", *a, **k),
    )
    monkeypatch.setattr(
        fixture.client,
        session_method,
        lambda *a, **k: remote(original_session, "session", *a, **k),
    )
    ready = await admission.admit_capture(
        **call_arguments(fixture, resume=resume), configured_capture=selection
    )
    assert ready.status == "admitted"
    assert seen == (["session"] if resume else ["consent", "session"])
    assert len(authorizations) == (2 if resume else 3)
    assert journal(fixture).lease_token is None
    assert ready.configured_capture == selection


def test_first_claim_is_durable_and_competing_claim_cannot_dispatch(admission_case):
    fixture = admission_case
    claim = first_claim(fixture)
    retained = journal(fixture)
    assert str(retained.id) == claim.operation_ref
    assert retained.lease_token == claim.lease_token
    assert claim.phase == retained.lease_phase == "consent"
    with pytest.raises(admission.AdmissionInProgress):
        first_claim(fixture)
    assert journal(fixture).lease_token == claim.lease_token
    assert fixture.client.calls == []


def test_cancelled_first_claim_rolls_back_both_preparation_and_lease(
    admission_case, monkeypatch
):
    fixture = admission_case
    budget = CaptureBudget(5)
    original = admission._claim_locked

    def cancelled(*args):
        result = original(*args)
        budget.cancel()
        return result

    monkeypatch.setattr(admission, "_claim_locked", cancelled)
    with capture_budget(budget), pytest.raises(CaptureDeadlineExceeded):
        first_claim(fixture)
    assert maybe_journal(fixture) is None
    assert fixture.client.calls == []


def test_failed_first_claim_does_not_leave_half_prepared_journal(
    admission_case, monkeypatch
):
    def failed(*_args):
        raise admission.AdmissionConflict

    monkeypatch.setattr(admission, "_claim_locked", failed)
    with pytest.raises(admission.AdmissionConflict):
        first_claim(admission_case)
    assert maybe_journal(admission_case) is None
    assert admission_case.client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["consent", "session"])
async def test_configured_lost_response_recovers_same_durable_session(
    admission_case, phase
):
    fixture = admission_case
    install_configured(fixture)
    selection = ConfiguredCaptureSelection(schema_version="1.0", language_mode="en-US")
    fixture.client.lose_response.add(phase)
    with pytest.raises(admission.AdmissionUnconfirmed):
        await admission.admit_capture(
            **call_arguments(fixture), configured_capture=selection
        )
    reservation = journal(fixture).configured_capture.copy()
    ready = await admission.admit_capture(
        **call_arguments(fixture, resume=True), configured_capture=selection
    )
    repeated = await admission.admit_capture(
        **call_arguments(fixture, resume=True), configured_capture=selection
    )
    assert ready == repeated
    assert str(ready.session_id) == reservation["id"]
    assert journal(fixture).configured_capture == reservation
    assert len(fixture.client.consents) == len(fixture.client.sessions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["consent", "session"])
async def test_configured_revocation_during_io_prevents_ready(admission_case, phase):
    from app.models.authority import AuthorityActorExternalIdentity
    from sqlalchemy import text
    from sqlmodel import Session

    fixture = admission_case
    install_configured(fixture)

    def revoke(returned, _receipt):
        if returned != phase:
            return
        with Session(fixture.engine) as session:
            session.execute(text("SET LOCAL lock_timeout = '1s'"))
            identity = session.get(
                AuthorityActorExternalIdentity, fixture.case.identity.id
            )
            identity.revoked_at = datetime.now(UTC)
            session.add(identity)
            session.commit()

    fixture.client.after = revoke
    selection = ConfiguredCaptureSelection(schema_version="1.0", language_mode="en-US")
    with pytest.raises(authority.AuthorityKernelError):
        await admission.admit_capture(
            **call_arguments(fixture), configured_capture=selection
        )
    row = journal(fixture)
    assert row.consent_id is not None
    assert (row.session_id is not None) == (phase == "session")
    assert len(fixture.configured_calls) == (0 if phase == "consent" else 1)


@pytest.mark.asyncio
async def test_resume_same_receipt_with_one_less_authority_transaction(
    admission_case, monkeypatch, record_property
):
    fixture = admission_case
    ready = await admission.admit_capture(**call_arguments(fixture))
    counts = Counter()

    def count(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT "):
            counts["selects"] += 1

    def commit(_connection):
        counts["commits"] += 1

    event.listen(fixture.engine, "before_cursor_execute", count)
    event.listen(fixture.engine, "commit", commit)
    original = admission._prepare_and_claim

    def previous(caller, binding_ref, workspace_id, *args, **kwargs):
        reference = admission._prepare(
            caller, binding_ref, workspace_id, *args, **kwargs
        )
        return admission._claim(caller, binding_ref, workspace_id, reference)

    try:
        monkeypatch.setattr(admission, "_prepare_and_claim", previous)
        before = await admission.admit_capture(**call_arguments(fixture, resume=True))
        previous_counts = counts.copy()
        counts.clear()
        monkeypatch.setattr(admission, "_prepare_and_claim", original)
        after = await admission.admit_capture(**call_arguments(fixture, resume=True))
        current_counts = counts.copy()
    finally:
        event.remove(fixture.engine, "before_cursor_execute", count)
        event.remove(fixture.engine, "commit", commit)
    assert before == after == ready
    assert previous_counts["commits"] == current_counts["commits"] + 1
    assert previous_counts["selects"] > current_counts["selects"]
    record_property("previous", dict(previous_counts))
    record_property("current", dict(current_counts))
