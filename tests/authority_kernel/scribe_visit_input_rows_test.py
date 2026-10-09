"""Exact visit read batching retains fresh checks and no unauthorized lineage."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services import authority, scribe_encounters
from app.services import doctor_access_row_ownership as owned

from app.models.authority import InteractionLineage
from app.models.scribe_capture_binding import (
    ScribeCaptureBinding,
    ScribeCaptureProviderLink,
)
from sqlalchemy import event, func
from sqlmodel import select

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    query_counts,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

engine, native, workflow = _engine, _native, _workflow


def binding_for(native, engine):
    with session_for(engine) as session:
        row = session.exec(
            select(ScribeCaptureBinding).where(
                ScribeCaptureBinding.workspace_id == native.row["workspace_id"]
            )
        ).one()
        return SimpleNamespace(id=row.id, provider_link_id=row.provider_link_id)


def prepare(native, binding, preparation):
    return native.post(
        action="preparation_context",
        preparation=preparation,
        binding_ref=str(binding.id),
        opening_nonce=None,
    )


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_complete_receipts_and_original_locks_match_separate_reads(
    native, engine, monkeypatch, kind, record_property
):
    results = []
    for previous in (True, False):
        locks = []

        def observe(_connection, _cursor, statement, *_):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if previous:
                patch.setattr(
                    scribe_encounters, "lookup_owns_subject_rows", lambda *_: False
                )
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as counts:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        results.append(
            (
                result["encounter"] if kind == "mount" else result["receipt"],
                counts["all_selects"],
                locks,
            )
        )
        assert engine.pool.checkedout() == 0
        assert owned._LOOKUP_ROWS.get() is None and owned._CAPTURE_ROWS.get() is None
    assert results[0][0] == results[1][0] and results[0][2] == results[1][2]
    assert results[0][1] - results[1][1] == (10 if kind == "mount" else 8)
    record_property("selects_removed", results[0][1] - results[1][1])
    record_property("exact_lock_sequence_preserved", True)


@pytest.mark.parametrize("preparation", ["admission", "recording_window"])
def test_preparation_keeps_three_lookups_with_six_fewer_reads(
    native, engine, monkeypatch, preparation, record_property
):
    from contextlib import contextmanager

    from app.services import scribe_native_capture as service

    binding = binding_for(native, engine)
    results = []
    original_phase = service.phase
    for previous in (True, False):
        phases, locks = [], []

        @contextmanager
        def phase(name):
            phases.append(name)
            with original_phase(name):
                yield

        def observe(_connection, _cursor, statement, *_):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if previous:
                patch.setattr(
                    scribe_encounters, "lookup_owns_subject_rows", lambda *_: False
                )
            patch.setattr(service, "phase", phase)
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as counts:
                    response = prepare(native, binding, preparation)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        for field in (
            "context_token",
            "interaction_ref",
            "client_turn_id",
            "checked_at",
            "request_nonce",
        ):
            result.pop(field)
        results.append((result, counts["all_selects"], locks, phases))
        assert phases.count("policy_lookup") == 3
        assert engine.pool.checkedout() == 0
    assert results[0][0] == results[1][0] and results[0][2:] == results[1][2:]
    assert results[0][1] - results[1][1] == 6
    record_property("preparation_selects_removed", 6)
    record_property("fresh_lookup_count", 3)


@pytest.mark.parametrize(
    "changed",
    [
        "appointment_doctor",
        "appointment_patient",
        "patient_doctor",
        "binding_actor",
        "binding_status",
    ],
)
def test_every_joined_owner_comparison_still_refuses(
    native, engine, monkeypatch, changed
):
    from typeid import TypeID

    from sqlalchemy.orm.attributes import set_committed_value

    original = scribe_encounters._visit_input_rows

    def rows(session, row):
        current = original(session, row)
        index, field = {
            "appointment_doctor": (0, "doctor_id"),
            "appointment_patient": (0, "patient_id"),
            "patient_doctor": (1, "doctor_id"),
            "binding_actor": (2, "actor_id"),
            "binding_status": (2, "status"),
        }[changed]
        value = (
            "revoked"
            if field == "status"
            else TypeID(getattr(current[index], field).prefix)
        )
        set_committed_value(current[index], field, value)
        return current

    monkeypatch.setattr(scribe_encounters, "_visit_input_rows", rows)
    assert native.post("creator_review").status_code == 403
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_missing_joined_components_refuse(native, engine, monkeypatch, missing):
    original = scribe_encounters._visit_input_rows

    def changed(session, row):
        current = list(original(session, row))
        current[missing] = None
        return tuple(current)

    monkeypatch.setattr(scribe_encounters, "_visit_input_rows", changed)
    assert native.post("creator_review").status_code == 403
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "transition", ["commit", "rollback", "savepoint", "expiration"]
)
def test_expired_lookup_ownership_uses_original_reads(
    native, engine, monkeypatch, transition
):
    original = scribe_encounters._visit
    observed = []

    def visit(session, row, **kwargs):
        assert owned.lookup_owns_subject_rows(
            session, kwargs["doctor_id"], kwargs["subject"]
        )
        if transition == "commit":
            session.commit()
        elif transition == "rollback":
            session.rollback()
        elif transition == "savepoint":
            with session.begin_nested():
                pass
        else:
            session.expire_all()
        assert not owned.lookup_owns_subject_rows(
            session, kwargs["doctor_id"], kwargs["subject"]
        )
        observed.append(True)
        return original(session, row, **kwargs)

    def forbidden(*_):
        pytest.fail("Expired row ownership must use original reads")

    monkeypatch.setattr(scribe_encounters, "_visit", visit)
    monkeypatch.setattr(scribe_encounters, "_visit_input_rows", forbidden)
    assert native.post("creator_review").status_code == 200
    assert observed and engine.pool.checkedout() == 0


def test_exact_subject_doctor_and_session_are_required(native, engine, monkeypatch):
    original = scribe_encounters._visit
    observed = []

    def visit(session, row, **kwargs):
        doctor, subject = kwargs["doctor_id"], kwargs["subject"]
        assert owned.lookup_owns_subject_rows(session, doctor, subject)
        assert not owned.lookup_owns_subject_rows(session, None, subject)
        assert not owned.lookup_owns_subject_rows(session, doctor, "user_wrong")
        with session_for(engine) as other:
            assert not owned.lookup_owns_subject_rows(other, doctor, subject)
        assert owned.lookup_owns_subject_rows(session, doctor, subject)
        observed.append(True)
        return original(session, row, **kwargs)

    monkeypatch.setattr(scribe_encounters, "_visit", visit)
    assert native.post("creator_review").status_code == 200
    assert observed and engine.pool.checkedout() == 0


@pytest.mark.parametrize("preparation", ["admission", "recording_window"])
@pytest.mark.parametrize("revoked", ["provider_link", "capture_binding"])
def test_committed_revocation_after_exchange_refuses_before_new_lineage(
    native, engine, monkeypatch, preparation, revoked
):
    binding = binding_for(native, engine)
    with session_for(engine) as session:
        before = session.exec(
            select(func.count()).select_from(InteractionLineage)
        ).one()
    original = authority.exchange_context_token
    changes = []

    def exchange(**values):
        result = original(**values)
        if values.get("session") is None:
            with session_for(engine) as session:
                model = (
                    ScribeCaptureProviderLink
                    if revoked == "provider_link"
                    else ScribeCaptureBinding
                )
                identifier = (
                    binding.provider_link_id
                    if revoked == "provider_link"
                    else binding.id
                )
                row = session.get(model, identifier)
                assert row is not None
                row.revoked_at = datetime.now(UTC)
                session.add(row)
                session.commit()
                changes.append(True)
        return result

    monkeypatch.setattr(authority, "exchange_context_token", exchange)
    assert prepare(native, binding, preparation).status_code == 403
    assert changes == [True]
    with session_for(engine) as session:
        assert (
            session.exec(select(func.count()).select_from(InteractionLineage)).one()
            == before
        )
    assert engine.pool.checkedout() == 0
