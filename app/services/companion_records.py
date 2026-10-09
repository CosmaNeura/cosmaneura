"""Read current Core rows under existing human/encounter authority; no writes."""

import hashlib
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event
from typing import Any, cast

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.companion_records import CompanionRecordSettings
from app.configuration.scribe_capture import ScribeCaptureSettings
from app.services.companion_record_contract import (
    MAX_NOTE_BYTES,
    MAX_NOTES,
    MAX_RESPONSE_BYTES,
    RecordAppointment,
    RecordContent,
    RecordInteraction,
    RecordPatient,
    RecordRequest,
    RecordResponse,
    RecordSnapshot,
    SavedNote,
    TranscriptSource,
    canonical,
    ordered_note_hash,
)
from app.services.scribe_capture_deadline import _sql_budget
from app.services.scribe_capture_scope import authorize_capture_binding
from app.utils.statement_timeout_scope import (
    acknowledge_record_timeout,
    consume_record_timeout,
    current_record_timeout_scope,
    record_timeout_scope,
    remember_timeout_setting,
    reusable_timeout_limits,
    timeout_setting_in_force,
)

from activemodel.session_manager import global_session
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from sqlalchemy import Text, event, func
from sqlalchemy import cast as sql_cast
from sqlalchemy.orm import noload
from sqlmodel import Session, col, select


class RecordUnavailable(Exception):
    pass


class RecordDeadline(RecordUnavailable):
    pass


@dataclass(frozen=True)
class RecordBudget:
    expires_at: float
    _cancelled: Event = field(default_factory=Event, repr=False, compare=False)

    def cancel(self) -> None:
        self._cancelled.set()


def check_deadline(deadline: float | RecordBudget) -> int:
    if isinstance(deadline, RecordBudget):
        if deadline._cancelled.is_set():
            raise RecordDeadline
        expires_at = deadline.expires_at
    else:
        expires_at = deadline
    remaining = int((expires_at - time.monotonic()) * 1000)
    if remaining <= 0:
        raise RecordDeadline
    return min(remaining, 15_000)


@contextmanager
def transaction_budget(session: Session, deadline: float | RecordBudget):
    """Normal PG transactions permit authority locks; every statement is bounded.

    Connection acquisition may finish after public cancellation. The first
    statement then refuses; no continuing SQL is admitted past the deadline.
    """
    check_deadline(deadline)
    connection = session.connection()
    if connection.dialect.name != "postgresql":
        raise RecordUnavailable

    def limits():
        statement_ms = check_deadline(deadline)
        lock_ms = min(check_deadline(deadline), 2000)
        return statement_ms, lock_ms

    def before_cursor_execute(
        _connection, cursor, _statement, _parameters, context, _many
    ):
        statement_ms, lock_ms = limits()
        capture = _sql_budget()
        combined = (
            capture is not None
            and context is not None
            and current_record_timeout_scope(connection) is scope
        )
        if combined and consume_record_timeout(context, scope, capture, "capture"):
            assert capture is not None
            # The capture hook just set the minimum of both fresh budgets for
            # this exact statement. Both cancellation fences still run after
            # its database call; no settings receipt survives this callback.
            capture.check()
            check_deadline(deadline)
            return
        if combined:
            assert capture is not None
            statement_ms = min(statement_ms, max(1, int(capture.check() * 1000)))
            lock_ms = min(lock_ms, max(1, min(2000, int(capture.check() * 1000))))
        # Integers only; raw cursor avoids event recursion. Both settings are
        # transaction-local and never alter another request or database policy.
        current_limits = statement_ms, lock_ms
        if not combined or not timeout_setting_in_force(
            connection, cursor, context, scope, capture, current_limits
        ):
            if combined:
                current_limits = reusable_timeout_limits(
                    connection, cursor, context, scope, current_limits
                )
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true), "
                "set_config('lock_timeout', %s, true)",
                tuple(map(str, current_limits)),
            )
        check_deadline(deadline)
        if combined:
            assert capture is not None
            capture.check()
            remember_timeout_setting(
                connection, cursor, context, scope, capture, current_limits
            )
            acknowledge_record_timeout(context, scope, capture, "record")

    with record_timeout_scope(connection, limits) as scope:
        event.listen(connection, "before_cursor_execute", before_cursor_execute)
        try:
            check_deadline(deadline)
            yield
        finally:
            event.remove(connection, "before_cursor_execute", before_cursor_execute)


def _scope(binding) -> tuple[str, ...]:
    return tuple(
        str(getattr(binding, key))
        for key in (
            "id",
            "workspace_id",
            "actor_id",
            "tenant_id",
            "doctor_id",
            "practice_id",
            "patient_id",
            "appointment_id",
            "scope_sha256",
        )
    )


def _row(session: Session, model, identifier):
    columns = {
        Appointment: ("id", "doctor_id", "patient_id", "appointment_date"),
        Patient: ("id", "doctor_id", "name"),
        DoctorNoteSchema: ("id", "doctor_id", "note_type", "deleted_at"),
        AIVisitTranscript: (
            "id",
            "doctor_id",
            "patient_id",
            "appointment_id",
            "created_at",
            "duration",
            "deleted_at",
        ),
    }
    query = (
        # Column rows avoid ActiveModel's reconstruction hook, which reads all
        # model fields even when an ORM load defers clinical text/instructions.
        select(*(getattr(model, key) for key in columns[model])).where(
            model.id == identifier
        )
    )
    if model is Patient:
        query = query.where(func.char_length(col(model.name)).between(1, 200))
    if model is DoctorNoteSchema:
        query = query.where(func.char_length(col(model.note_type)).between(1, 200))
    row = session.exec(
        query.with_for_update(read=True, of=model).execution_options(
            populate_existing=True
        )
    ).one_or_none()
    if row is None:
        raise RecordUnavailable
    return row


def _selection(session: Session, binding):
    # Check stored document sizes before materializing clinical text. The
    # transcript body and mutable template instructions are never selected.
    query = (
        select(
            col(AIVisitNote.id),
            col(AIVisitNote.doctor_note_schema_id),
            col(AIVisitNote.ai_transcript_id),
            func.octet_length(sql_cast(cast(Any, AIVisitNote.note), Text)),
        )
        .where(
            AIVisitNote.appointment_id == binding.appointment_id,
            cast(Any, AIVisitNote.superseded_at).is_(None),
        )
        .order_by(col(AIVisitNote.doctor_note_schema_id), col(AIVisitNote.id))
        .limit(MAX_NOTES + 1)
    )
    rows = session.exec(query).all()
    if (
        len(rows) > MAX_NOTES
        or any(
            type(row[3]) is not int or not 1 <= row[3] <= MAX_NOTE_BYTES for row in rows
        )
        or sum(row[3] for row in rows) > MAX_RESPONSE_BYTES
    ):
        raise RecordUnavailable
    return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def _current(session: Session, binding):
    query = (
        select(AIVisitNote)
        .where(
            AIVisitNote.appointment_id == binding.appointment_id,
            AIVisitNote.doctor_id == binding.doctor_id,
            cast(Any, AIVisitNote.superseded_at).is_(None),
            func.octet_length(sql_cast(cast(Any, AIVisitNote.note), Text)).between(
                1, MAX_NOTE_BYTES
            ),
        )
        .options(noload(cast(Any, AIVisitNote.doctor_note_schema)))
        .order_by(col(AIVisitNote.doctor_note_schema_id), col(AIVisitNote.id))
        .limit(MAX_NOTES + 1)
    )
    query = query.with_for_update(read=True, of=AIVisitNote)
    notes = session.exec(query.execution_options(populate_existing=True)).all()
    if len(notes) > MAX_NOTES:
        raise RecordUnavailable
    return notes


def project_record(session: Session, binding) -> RecordContent:
    """Caller already holds the inherited appointment/authority locks."""
    appointment = _row(session, Appointment, binding.appointment_id)
    patient = _row(session, Patient, binding.patient_id)
    if (
        appointment.doctor_id != binding.doctor_id
        or appointment.patient_id != patient.id
        or patient.doctor_id != binding.doctor_id
    ):
        raise RecordUnavailable
    selection = _selection(session, binding)
    # Resolve IDs first, then lock shared schemas/sources deterministically before
    # locking note rows. The existing authority helper already locked appointment.
    schemas = {
        ref: _row(session, DoctorNoteSchema, ref)
        for ref in sorted({item[1] for item in selection})
    }
    transcripts = {
        ref: _row(session, AIVisitTranscript, ref)
        for ref in sorted({item[2] for item in selection})
    }
    notes = _current(session, binding)
    if selection != [
        (str(n.id), str(n.doctor_note_schema_id), str(n.ai_transcript_id))
        for n in notes
    ]:
        raise RecordUnavailable
    projected = []
    for note in notes:
        schema = schemas[str(note.doctor_note_schema_id)]
        if (
            note.doctor_id != binding.doctor_id
            or note.appointment_id != appointment.id
            or note.superseded_at is not None
            or schema.doctor_id != binding.doctor_id
        ):
            raise RecordUnavailable
        if not isinstance(note.note, dict):
            raise RecordUnavailable
        fields = dict(note.note)
        order = tuple(fields)
        projected.append(
            SavedNote(
                note_ref=str(note.id),
                schema_ref=str(schema.id),
                note_type=schema.note_type,
                schema_deleted=schema.deleted_at is not None,
                transcript_ref=str(note.ai_transcript_id),
                fields=fields,
                field_order=order,
                note_sha256=ordered_note_hash(fields, order),
                created_at=note.created_at,
            )
        )
    sources = []
    for ref, transcript in sorted(transcripts.items()):
        if (
            transcript.doctor_id != binding.doctor_id
            or transcript.appointment_id != appointment.id
            or transcript.patient_id != patient.id
        ):
            raise RecordUnavailable
        sources.append(
            TranscriptSource(
                transcript_ref=ref,
                availability="deleted"
                if transcript.deleted_at is not None
                else "retained",
                created_at=transcript.created_at,
                duration_seconds=transcript.duration,
            )
        )
    return RecordContent(
        actor_ref=str(binding.actor_id),
        tenant_ref=str(binding.tenant_id),
        doctor_ref=str(binding.doctor_id),
        practice_ref=str(binding.practice_id),
        patient=RecordPatient(patient_ref=str(patient.id), display_name=patient.name),
        appointment=RecordAppointment(
            appointment_ref=str(appointment.id),
            patient_ref=str(patient.id),
            appointment_date=appointment.appointment_date,
            timezone_known=appointment.appointment_date.utcoffset() is not None,
        ),
        notes=tuple(projected),
        transcript_sources=tuple(sources),
    )


def read_record(
    *,
    principal_client_id: str,
    interaction: RecordInteraction,
    request: RecordRequest,
    settings: CompanionRecordSettings,
    authority_settings: AuthorityKernelSettings,
    capture_settings: ScribeCaptureSettings,
    deadline: float | RecordBudget,
) -> RecordResponse:
    if not settings.enabled:
        raise RecordUnavailable
    check_deadline(deadline)
    # The route starts a fresh Context. Establish one worker-owned ActiveModel
    # session so nested legacy .get() reads use these same locks and SQL budgets.
    with global_session() as managed:
        session = cast(Session, managed)
        try:
            with session.no_autoflush, transaction_budget(session, deadline):

                def authorize():
                    return authorize_capture_binding(
                        session,
                        binding_ref=request.binding_ref,
                        workspace_id=request.workspace_id,
                        workload_client_id=principal_client_id,
                        mount_context_token=interaction.mount_context_token,
                        interaction_ref=interaction.interaction_ref,
                        client_turn_id=interaction.client_turn_id,
                        settings=authority_settings,
                        capture_settings=capture_settings,
                    )

                binding = authorize()
                scope = _scope(binding)
                first = project_record(session, binding)
                check_deadline(deadline)
                latest = project_record(session, binding)
                if latest != first:
                    raise RecordUnavailable
                # Recheck current human/session/practice authority after the final
                # source read, while source locks still protect this observation.
                current = authorize()
                if (
                    _scope(current) != scope
                    or session.new
                    or session.dirty
                    or session.deleted
                ):
                    raise RecordUnavailable
                check_deadline(deadline)
                data = latest.model_dump(mode="json")
                snapshot = RecordSnapshot(
                    **data, snapshot_sha256=hashlib.sha256(canonical(data)).hexdigest()
                )
                response = RecordResponse(
                    **request.model_dump(),
                    checked_at=datetime.now(UTC),
                    record=snapshot,
                )
                check_deadline(deadline)
                return response
        finally:
            # No authority issuance/admission/bookkeeping is committed by this
            # reader. Rollback also releases locks on success and late timeout.
            session.rollback()
