"""Repository doubles exercise application decisions; no PG proof is claimed."""

import time
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from companion_record_contract_test import ref, sample

from app.configuration.companion_records import CompanionRecordSettings
from app.services import companion_records as service
from app.services.companion_record_contract import (
    MAX_NOTE_BYTES,
    RecordInteraction,
    RecordRequest,
    RecordResponse,
)

from sqlalchemy.dialects import postgresql
from sqlmodel import Session


def fixtures():
    stamp = datetime(2020, 1, 1, tzinfo=UTC).replace(
        tzinfo=None
    )  # Retained legacy naive source.
    binding = SimpleNamespace(
        id=ref("scribe_capture_binding"),
        workspace_id="00000000-0000-4000-8000-000000000001",
        actor_id=ref("authority_actor"),
        tenant_id=ref("authority_tenant"),
        doctor_id=ref("doctor"),
        practice_id=ref("practice"),
        patient_id=ref("patient"),
        appointment_id=ref("appointment"),
        scope_sha256="a" * 64,
    )
    patient = SimpleNamespace(
        id=binding.patient_id, doctor_id=binding.doctor_id, name="Invented Person"
    )
    appointment = SimpleNamespace(
        id=binding.appointment_id,
        doctor_id=binding.doctor_id,
        patient_id=binding.patient_id,
        appointment_date=stamp,
    )
    schema = SimpleNamespace(
        id=ref("doctor_note_schema"),
        doctor_id=binding.doctor_id,
        note_type="Invented SOAP",
        deleted_at=None,
        fields={"different": "Do not use template instructions"},
    )
    transcript = SimpleNamespace(
        id=ref("ai_visit_transcript"),
        doctor_id=binding.doctor_id,
        patient_id=binding.patient_id,
        appointment_id=binding.appointment_id,
        created_at=stamp,
        deleted_at=None,
        duration=90,
        transcript_data="MUST NOT SELECT",
    )
    note = SimpleNamespace(
        id=ref("visit_note"),
        doctor_id=binding.doctor_id,
        appointment_id=binding.appointment_id,
        doctor_note_schema_id=schema.id,
        ai_transcript_id=transcript.id,
        note={"Plan": "Invented plan", "History": "Invented history"},
        superseded_at=None,
        created_at=stamp,
    )
    rows: dict[type, SimpleNamespace | None] = {
        service.Patient: patient,
        service.Appointment: appointment,
        service.DoctorNoteSchema: schema,
        service.AIVisitTranscript: transcript,
        service.AIVisitNote: note,
    }
    return binding, rows


class Result:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values

    def one_or_none(self):
        return self.values[0] if self.values else None


class Repository:
    def __init__(self, rows):
        self.session = Session()
        # Inject only the query executor; production still receives a real Session.
        setattr(self.session, "exec", self.exec)
        self.rows, self.sql = rows, []
        self.new, self.dirty, self.deleted = set(), set(), set()
        self.rollbacks = 0
        self.no_autoflush = nullcontext()

    def exec(self, query):
        self.sql.append(str(query.compile(dialect=postgresql.dialect())))
        descriptions = query.column_descriptions
        entity = descriptions[0]["entity"]
        row = self.rows.get(entity)
        if entity is service.AIVisitNote and len(descriptions) > 1:
            if row is None:
                return Result([])
            return Result(
                [
                    (
                        row.id,
                        row.doctor_note_schema_id,
                        row.ai_transcript_id,
                        len(str(row.note).encode()),
                    )
                ]
            )
        return Result([row] if row is not None else [])

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_real_query_projection_uses_saved_order_no_admission_and_metadata_only_sources():
    binding, rows = fixtures()
    repository = Repository(rows)
    value = service.project_record(repository.session, binding)
    assert tuple(value.notes[0].fields) == ("Plan", "History")
    assert value.transcript_sources[0].availability == "retained"
    assert "session_id" not in value.model_dump_json()
    transcript_sql = next(s for s in repository.sql if "FROM ai_visit_transcript" in s)
    schema_sql = next(s for s in repository.sql if "FROM doctor_note_schema" in s)
    assert (
        "transcript_data" not in transcript_sql
        and "doctor_note_schema.fields" not in schema_sql
    )
    assert "octet_length" in repository.sql[2]
    assert all(
        "FOR SHARE" in s
        for s in repository.sql
        if "octet_length" not in s or "ai_visit_note.note," in s
    )


@pytest.mark.parametrize(
    "target,field",
    [
        (service.Patient, "doctor_id"),
        (service.Appointment, "patient_id"),
        (service.Appointment, "doctor_id"),
        (service.AIVisitNote, "doctor_id"),
        (service.AIVisitNote, "appointment_id"),
        (service.DoctorNoteSchema, "doctor_id"),
        (service.AIVisitTranscript, "doctor_id"),
        (service.AIVisitTranscript, "patient_id"),
        (service.AIVisitTranscript, "appointment_id"),
    ],
)
def test_cross_scope_source_denied(target, field):
    binding, rows = fixtures()
    setattr(rows[target], field, "different_scope")
    with pytest.raises(service.RecordUnavailable):
        service.project_record(Repository(rows).session, binding)


def test_deletion_preserved_and_empty_notes_do_not_read_or_invent_transcript():
    binding, rows = fixtures()
    transcript, schema = rows[service.AIVisitTranscript], rows[service.DoctorNoteSchema]
    assert transcript is not None and schema is not None
    transcript.deleted_at = datetime(2021, 1, 1, tzinfo=UTC)
    schema.deleted_at = datetime(2021, 1, 1, tzinfo=UTC)
    value = service.project_record(Repository(rows).session, binding)
    assert (
        value.notes[0].schema_deleted
        and value.transcript_sources[0].availability == "deleted"
    )
    rows[service.AIVisitNote] = None
    repository = Repository(rows)
    empty = service.project_record(repository.session, binding)
    assert empty.notes == empty.transcript_sources == ()
    assert not any("FROM ai_visit_transcript" in s for s in repository.sql)


def test_oversized_stored_document_refused_before_full_text_query():
    binding, rows = fixtures()
    note = rows[service.AIVisitNote]
    assert note is not None
    note.note = {"Plan": "x" * (MAX_NOTE_BYTES + 1)}
    repository = Repository(rows)
    with pytest.raises(service.RecordUnavailable):
        service.project_record(repository.session, binding)
    assert len(repository.sql) == 3


def test_current_selection_change_is_not_silently_accepted(monkeypatch):
    binding, rows = fixtures()
    monkeypatch.setattr(service, "_current", lambda *_: [])
    with pytest.raises(service.RecordUnavailable):
        service.project_record(Repository(rows).session, binding)


def admitted(monkeypatch):
    binding, rows = fixtures()
    repository = Repository(rows)
    calls = []

    @contextmanager
    def database():
        yield repository

    monkeypatch.setattr(service, "global_session", database)
    monkeypatch.setattr(service, "transaction_budget", lambda *_: nullcontext())

    def authorize(*_args, **_kwargs):
        calls.append("authorize")
        return binding

    monkeypatch.setattr(service, "authorize_capture_binding", authorize)
    value = sample()
    request = RecordRequest.model_validate(
        {k: value[k] for k in ("binding_ref", "workspace_id", "request_nonce")}
    )
    kwargs = {
        "principal_client_id": "00000000-0000-4000-8000-000000000004",
        "interaction": RecordInteraction(
            mount_context_token="invented-token-only",
            interaction_ref="invented-interaction",
            client_turn_id=UUID("00000000-0000-4000-8000-000000000003"),
        ),
        "request": request,
        "settings": CompanionRecordSettings(True),
        "authority_settings": None,
        "capture_settings": None,
        "deadline": time.monotonic() + 15,
    }
    return repository, binding, calls, kwargs


def test_application_rechecks_source_then_authority_and_always_rolls_back(monkeypatch):
    repository, _, calls, kwargs = admitted(monkeypatch)
    original = service.project_record

    def projection(*args):
        calls.append("source")
        return original(*args)

    monkeypatch.setattr(service, "project_record", projection)
    response = service.read_record(**kwargs)
    assert isinstance(response, RecordResponse)
    assert calls == ["authorize", "source", "source", "authorize"]
    assert repository.rollbacks == 1


@pytest.mark.parametrize(
    "failure", ["source", "authority", "scope", "dirty", "deadline"]
)
def test_late_failure_never_releases_record_and_releases_transaction(
    monkeypatch, failure
):
    repository, binding, calls, kwargs = admitted(monkeypatch)
    original = service.project_record
    count = 0

    def projection(*args):
        nonlocal count
        count += 1
        value = original(*args)
        if count == 2:
            if failure == "source":
                return value.model_copy(
                    update={
                        "patient": value.patient.model_copy(
                            update={"display_name": "Changed"}
                        )
                    }
                )
            if failure == "scope":
                binding.scope_sha256 = "b" * 64
            if failure == "dirty":
                repository.dirty.add("unexpected_write")
            if failure == "deadline":
                kwargs["deadline"] = 0  # separately expired below
        return value

    monkeypatch.setattr(service, "project_record", projection)
    if failure == "authority":

        def authorize(*_a, **_k):
            calls.append("authorize")
            if len(calls) > 1:
                raise service.RecordUnavailable
            return binding

        monkeypatch.setattr(service, "authorize_capture_binding", authorize)
    if failure == "deadline":
        ticks = iter([100.0, 100.0, 100.0, 200.0])
        kwargs["deadline"] = 150.0
        monkeypatch.setattr(service.time, "monotonic", lambda: next(ticks))
    with pytest.raises(service.RecordUnavailable):
        service.read_record(**kwargs)
    assert repository.rollbacks == 1


def test_disabled_read_never_acquires_session(monkeypatch):
    _, _, _, kwargs = admitted(monkeypatch)
    kwargs["settings"] = CompanionRecordSettings()
    monkeypatch.setattr(
        service, "global_session", lambda: pytest.fail("must not acquire")
    )
    with pytest.raises(service.RecordUnavailable):
        service.read_record(**kwargs)


def test_worker_global_session_contains_nested_legacy_reads(monkeypatch):
    # Actual ActiveModel context handling, with only Session creation replaced.
    # This catches get_session() alone creating separate nested transactions.
    from activemodel import session_manager

    repository, binding, _, kwargs = admitted(monkeypatch)
    monkeypatch.setattr(service, "global_session", session_manager.global_session)
    monkeypatch.setattr(
        session_manager.SessionManager.get_instance(), "get_engine", object
    )
    monkeypatch.setattr(session_manager, "Session", lambda _engine: repository)
    observed = []

    def authorize(*_args, **_kwargs):
        with session_manager.get_session() as nested:
            observed.append(nested is repository)
        return binding

    monkeypatch.setattr(service, "authorize_capture_binding", authorize)
    service.read_record(**kwargs)
    assert observed == [True, True] and repository.rollbacks == 1
    assert session_manager._session_context.get() is None


def test_over_capacity_selection_never_loads_full_note_text():
    binding, rows = fixtures()

    class TooMany(Repository):
        def exec(self, query):
            result = super().exec(query)
            if len(query.column_descriptions) > 1:
                return Result(result.values * 21)
            return result

    repository = TooMany(rows)
    with pytest.raises(service.RecordUnavailable):
        service.project_record(repository.session, binding)
    assert len(repository.sql) == 3


def test_statement_guard_refuses_late_sql_and_restores_listener(monkeypatch):
    listener = {}
    connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    session = Session()
    monkeypatch.setattr(session, "connection", lambda: connection)
    monkeypatch.setattr(
        service.event,
        "listen",
        lambda _c, _name, callback: listener.update(callback=callback),
    )
    monkeypatch.setattr(
        service.event, "remove", lambda *_: listener.update(removed=True)
    )
    commands = []
    cursor = SimpleNamespace(execute=lambda sql, values: commands.append((sql, values)))
    with service.transaction_budget(session, time.monotonic() + 10):
        listener["callback"](None, cursor, None, None, None, None)
        assert commands[0][0] == (
            "SELECT set_config('statement_timeout', %s, true), "
            "set_config('lock_timeout', %s, true)"
        )
        assert 0 < int(commands[0][1][1]) <= 2000
        monkeypatch.setattr(service.time, "monotonic", lambda: 1_000_000_000_000.0)
        with pytest.raises(service.RecordDeadline):
            listener["callback"](None, cursor, None, None, None, None)
        assert len(commands) == 1
    assert listener["removed"]


def test_cancelled_sql_setting_never_admits_following_statement(monkeypatch):
    listener, commands = {}, []
    budget = service.RecordBudget(time.monotonic() + 10)
    connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    session = Session()
    monkeypatch.setattr(session, "connection", lambda: connection)
    monkeypatch.setattr(
        service.event,
        "listen",
        lambda _c, _name, callback: listener.update(callback=callback),
    )
    monkeypatch.setattr(
        service.event, "remove", lambda *_: listener.update(removed=True)
    )

    def execute(sql, values):
        commands.append((sql, values))
        budget.cancel()

    with (
        service.transaction_budget(session, budget),
        pytest.raises(service.RecordDeadline),
    ):
        listener["callback"](
            None, SimpleNamespace(execute=execute), None, None, None, None
        )
        commands.append("forbidden-original-statement")
    assert len(commands) == 1 and listener["removed"]


def test_cancelled_budget_refuses_session_acquisition(monkeypatch):
    _, _, _, kwargs = admitted(monkeypatch)
    kwargs["deadline"] = service.RecordBudget(time.monotonic() + 10)
    kwargs["deadline"].cancel()
    monkeypatch.setattr(
        service, "global_session", lambda: pytest.fail("must not acquire")
    )
    with pytest.raises(service.RecordDeadline):
        service.read_record(**kwargs)
