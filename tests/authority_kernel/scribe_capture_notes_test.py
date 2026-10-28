"""Real local persistence, synthetic transcript/model, no external calls."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.commands import scribe_transcript as ingest
from app.services import authority
from app.services import scribe_capture_notes as notes
from app.services.scribe_capture_gateway import CaptureOperationUnavailable
from app.services.scribe_capture_plan_grounding import CONTRACT_END, CONTRACT_START
from app.services.scribe_workflow import CaptureTranscript, ScribeWorkflowRejectedError

from activemodel.session_manager import _session_context
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.scribe_transcript_delivery import ScribeTranscriptDelivery
from sqlmodel import Session, select

from tests.authority_kernel.scribe_capture_gateway_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_gateway_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_capture_gateway_test import pair, ready

admission_case = _admission_case
capture_engine = _capture_engine


class FakeModel:
    def __init__(self):
        self.calls = []
        self.after = None
        self.output = '{"Plan":"","CC":"Sleep"}'

    async def complete(self, prompt, *, json_output):
        self.calls.append((prompt, json_output))
        if self.after:
            self.after(len(self.calls))
        if json_output and CONTRACT_START in prompt:
            contract = json.loads(
                prompt.rsplit(CONTRACT_START, 1)[1].split(CONTRACT_END)[0]
            )
            refs = {field: [] for field in contract["prospective_fields"]}
            # Preserve deliberately malformed/duplicate-key output for the real
            # production validator rather than normalizing it in this fixture.
            return (
                '{"fields":'
                + self.output
                + ',"plan_source_refs":'
                + json.dumps(refs)
                + "}"
            )
        return self.output if json_output else "- Discuss sleep at follow-up."


async def setup(f, monkeypatch) -> dict[str, Any]:
    scope = await ready(f)
    _, f.client.result = pair("transcript", scope)
    native = f.client.capture_operation

    async def operation(tenant, session, payload, *, idempotency_key):
        if payload.kind == "transcription_status":
            return await f.client.get_capture_status(tenant, session)
        return await native(tenant, session, payload, idempotency_key=idempotency_key)

    f.client.capture_operation = operation
    f.caller = replace(
        f.caller,
        capture_settings=replace(f.caller.capture_settings, notes_enabled=True),
    )
    with Session(f.engine) as session:
        schema = DoctorNoteSchema(
            doctor_id=f.case.doctor.id,
            note_type="Synthetic visit",
            fields={"CC": "Chief complaint", "Plan": "Explicitly discussed plan"},
        )
        session.add(schema)
        session.commit()
        f.schema_ref = str(schema.id)
    f.model = FakeModel()
    f.scope = scope
    monkeypatch.setattr(
        ingest,
        "_dispatch_pipeline_if_needed",
        lambda *_: pytest.fail("legacy dispatch reached"),
    )
    return {
        "caller": f.caller,
        "binding_ref": f.binding.binding_ref,
        "workspace_id": f.binding.workspace_id,
        "expected_session_id": scope.session_id,
        "schema_ref": f.schema_ref,
        "prepare": True,
        "client": f.client,
        "model": f.model,
    }


def rows(f, model):
    with Session(f.engine) as session:
        return list(
            session.exec(
                select(model).where(model.appointment_id == f.case.appointment.id)
            ).all()
        )


@pytest.mark.asyncio
async def test_prepare_retry_read_and_lost_response_recover_one_source_and_note(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    absent = await notes.capture_note(**{**kwargs, "prepare": False})
    assert absent.status == "not_prepared"
    assert absent.fields is absent.note_ref is absent.transcript_ref is None
    assert not f.model.calls and not rows(f, AIVisitTranscript)
    first = await notes.capture_note(**kwargs)
    # Deliberately ignore a response, then recover with a new call/context read.
    second = await notes.capture_note(**kwargs)
    reopened = await notes.capture_note(**{**kwargs, "prepare": False})
    assert first == second == reopened
    assert first.review_required and first.status == "draft"
    assert first.fields is not None
    assert list(first.fields) == ["CC", "Plan"]
    assert first.fields is not None
    assert "Greater than" not in first.fields["Plan"]
    assert len(f.model.calls) == 1
    assert "infer it from the clinical context" not in f.model.calls[0][0]
    assert "example_note" not in f.model.calls[0][0]
    assert len(rows(f, AIVisitTranscript)) == len(rows(f, AIVisitNote)) == 1
    delivery = rows(f, ScribeTranscriptDelivery)[0]
    assert delivery.pipeline_dispatched_at is None
    assert "Discuss sleep" not in repr(first)


@pytest.mark.asyncio
async def test_capture_drafts_once_from_source_without_an_intermediate_action_list(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)

    def unexpected_extraction(*args, **kwargs):
        pytest.fail("capture must not extract a second generated source")

    monkeypatch.setattr(notes.summarize, "extract_action_items", unexpected_extraction)
    monkeypatch.setattr(
        notes.summarize, "build_action_item_prompt", unexpected_extraction
    )
    source = f.client.result
    source_content = tuple(segment.content for segment in source.segments)
    result = await notes.capture_note(**kwargs)
    assert result.review_required and result.status == "draft"
    assert len(f.model.calls) == 1
    prompt, json_output = f.model.calls[0]
    assert json_output and all(content in prompt for content in source_content)
    assert "- Discuss sleep at follow-up." not in prompt  # Fake extractor output.
    assert len(rows(f, AIVisitNote)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["identity", "schema", "source", "consent"])
async def test_pending_generation_rechecks_scope_and_inputs(
    admission_case, monkeypatch, mutation
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)

    def change(count):
        if count != 1:
            return
        if mutation == "consent":

            async def denied(*_):
                raise ScribeWorkflowRejectedError("private sentinel")

            f.client.get_capture_status = denied
            return
        with Session(f.engine) as session:
            if mutation == "identity":
                row = session.get(AuthorityActorExternalIdentity, f.case.identity.id)
                assert row is not None
                row.revoked_at = datetime.now(UTC)
            elif mutation == "schema":
                row = session.get(DoctorNoteSchema, f.schema_ref)
                assert row is not None
                row.fields = {"Different": "Changed during generation"}
            else:
                row = session.exec(
                    select(AIVisitTranscript).where(
                        AIVisitTranscript.appointment_id == f.case.appointment.id
                    )
                ).one()
                row.transcript_data = [
                    row.transcript_data[0].model_copy(
                        update={"content": "Changed synthetic source"}
                    )
                ]
            session.add(row)
            session.commit()

    f.model.after = change
    error = (
        authority.AuthorityKernelError
        if mutation == "identity"
        else (
            CaptureOperationUnavailable
            if mutation == "consent"
            else notes.CaptureNoteConflict
        )
    )
    with pytest.raises(error) as failure:
        await notes.capture_note(**kwargs)
    assert "private sentinel" not in str(failure.value)
    assert len(f.model.calls) == 1
    assert not rows(f, AIVisitNote)
    assert len(rows(f, AIVisitTranscript)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        "not JSON",
        "[]",
        '{"CC":"a","CC":"b","Plan":"x"}',
        '{"CC":"only"}',
        '{"CC":42,"Plan":"x"}',
        '{"CC":"x","Plan":"y","extra":"z"}',
        '"private sentinel"',
        "x" * 262_145,
    ],
    ids=[
        "non_json",
        "array",
        "duplicate",
        "missing",
        "non_string",
        "extra",
        "scalar",
        "oversize",
    ],
)
async def test_invalid_output_retains_source_and_retry_can_succeed(
    admission_case, monkeypatch, output
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    f.model.output = output
    with pytest.raises(notes.CaptureNoteUnconfirmed) as error:
        await notes.capture_note(**kwargs)
    assert not str(error.value)
    assert not rows(f, AIVisitNote)
    f.model.output = '{"CC":"Sleep","Plan":""}'
    assert (await notes.capture_note(**kwargs)).status == "draft"
    assert len(rows(f, AIVisitTranscript)) == len(rows(f, AIVisitNote)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["disabled", "session", "foreign_schema", "deleted_schema"]
)
async def test_scope_selection_cannot_import_or_invoke_model(
    admission_case, monkeypatch, fault
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    if fault == "disabled":
        kwargs["caller"] = replace(
            f.caller,
            capture_settings=replace(f.caller.capture_settings, notes_enabled=False),
        )
    elif fault == "session":
        kwargs["expected_session_id"] = uuid4()
    else:
        with Session(f.engine) as session:
            schema = session.get(DoctorNoteSchema, f.schema_ref)
            if fault == "deleted_schema":
                assert schema is not None
                schema.deleted_at = datetime.now(UTC)
            else:
                # A second real fixture provides a valid foreign-key owner.
                from tests.authority_kernel.scribe_capture_scope_test import (
                    make_scope_case,
                )

                other = make_scope_case(session)
                assert schema is not None
                schema.doctor_id = other.doctor.id
            session.add(schema)
            session.commit()
    with pytest.raises(notes.CaptureNoteError):
        await notes.capture_note(**kwargs)
    assert not f.model.calls and not rows(f, AIVisitTranscript)


@pytest.mark.asyncio
async def test_cancellation_after_import_never_publishes_late_result(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    entered = asyncio.Event()

    class PausedModel:
        async def complete(self, *_, **__):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(notes.capture_note(**{**kwargs, "model": PausedModel()}))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not rows(f, AIVisitNote)
    assert len(rows(f, AIVisitTranscript)) == 1
    assert (await notes.capture_note(**kwargs)).status == "draft"


@pytest.mark.asyncio
async def test_concurrent_generation_publishes_one_note(admission_case, monkeypatch):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    entered = 0
    both = asyncio.Event()

    class ConcurrentModel(FakeModel):
        async def complete(self, prompt, *, json_output):
            nonlocal entered
            if json_output:
                entered += 1
                if entered == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), 5)
            return await super().complete(prompt, json_output=json_output)

    async def run():
        with Session(f.engine, expire_on_commit=False) as session:
            context = _session_context.set(session)
            try:
                return await notes.capture_note(
                    **{**kwargs, "model": ConcurrentModel()}
                )
            finally:
                _session_context.reset(context)

    first, second = await asyncio.gather(run(), run())
    assert first.note_ref == second.note_ref
    assert len(rows(f, AIVisitNote)) == len(rows(f, AIVisitTranscript)) == 1


def test_source_digest_matches_existing_delivery_contract():
    from app.routes.service import ScribeTranscriptDelivery as Contract
    from app.routes.service import _payload_sha256

    from tests.authority_kernel.scribe_gateway_receiver_test import scope

    admitted = scope()
    admitted = replace(
        admitted,
        session_request=admitted.session_request.model_copy(
            update={"appointment_id": "appointment_01j00000000000000000000000"}
        ),
    )
    _, transcript = pair("transcript", admitted)
    assert isinstance(transcript, CaptureTranscript)
    source = notes._source(transcript, admitted)
    contract = Contract.model_validate(
        {
            "schema_version": "1.0",
            "encounter_id": admitted.session_request.appointment_id,
            "scribe_session_id": admitted.session_id,
            "duration_seconds": source.duration,
            "segments": [
                {
                    "speaker": e.speaker,
                    "content": e.content,
                    "start_ms": e.startTime,
                    "end_ms": e.endTime,
                }
                for e in source.entries
            ],
        }
    )
    assert source.digest == _payload_sha256(contract)


def test_note_shape_is_ordered_and_rejects_nested_private_values():
    assert list(
        notes._validated_note('{"Plan":"b","CC":"a"}', {"CC": "", "Plan": ""})
    ) == ["CC", "Plan"]
    with pytest.raises(notes.CaptureNoteUnconfirmed):
        notes._validated_note(
            json.dumps({"CC": {"private sentinel": True}}), {"CC": ""}
        )


@pytest.mark.asyncio
async def test_finalization_rechecks_authority_in_the_publication_transaction(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    original = notes.summarize._finalize_generated_note

    def revoke_then_finalize(**values):
        with Session(f.engine) as session:
            identity = session.get(AuthorityActorExternalIdentity, f.case.identity.id)
            assert identity is not None
            identity.revoked_at = datetime.now(UTC)
            session.add(identity)
            session.commit()
        return original(**values)

    monkeypatch.setattr(
        notes.summarize, "_finalize_generated_note", revoke_then_finalize
    )
    with pytest.raises(authority.AuthorityKernelError):
        await notes.capture_note(**kwargs)
    assert len(f.model.calls) == 1
    assert not rows(f, AIVisitNote)


@pytest.mark.asyncio
async def test_concurrent_restored_current_version_is_not_overwritten(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)

    def restore(count):
        if count != 1:
            return
        with Session(f.engine) as session:
            transcript = session.exec(
                select(AIVisitTranscript).where(
                    AIVisitTranscript.appointment_id == f.case.appointment.id
                )
            ).one()
            current = AIVisitNote(
                doctor_id=transcript.doctor_id,
                appointment_id=transcript.appointment_id,
                ai_transcript_id=transcript.id,
                doctor_note_schema_id=f.schema_ref,
                restore_frontier_transcript_id=transcript.id,
                note={
                    "CC": "Restored synthetic text",
                    "Plan": "Preserved clinician choice",
                },
            )
            session.add(current)
            session.commit()

    f.model.after = restore
    result = await notes.capture_note(**kwargs)
    assert result.fields is not None
    assert result.fields["Plan"] == "Preserved clinician choice"
    assert len(rows(f, AIVisitNote)) == 1


@pytest.mark.asyncio
async def test_model_timeout_retains_source_for_explicit_retry(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)

    class FailedModel:
        async def complete(self, *_, **__):
            raise TimeoutError("private sentinel")

    with pytest.raises(notes.CaptureNoteUnconfirmed) as error:
        await notes.capture_note(**{**kwargs, "model": FailedModel()})
    assert not str(error.value)
    assert len(rows(f, AIVisitTranscript)) == 1
    assert not rows(f, AIVisitNote)
    assert (await notes.capture_note(**kwargs)).status == "draft"


@pytest.mark.asyncio
async def test_configured_model_uses_gpt41_bounded_calls_and_no_automatic_retry(
    monkeypatch,
):
    from types import SimpleNamespace

    seen = []

    def complete(**values):
        seen.append(values)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="synthetic output"),
                )
            ]
        )

    def options(**values):
        assert values == {"timeout": 30.0, "max_retries": 0}
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=complete))
        )

    monkeypatch.setattr(notes.openai, "with_options", options)
    assert (
        await notes.ConfiguredNoteModel().complete("synthetic prompt", json_output=True)
        == "synthetic output"
    )
    assert seen[0]["model"] == "gpt-4.1-2025-04-14"
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert seen[0]["messages"][0]["content"] == notes.GROUNDING


@pytest.mark.asyncio
async def test_existing_core_content_is_linked_once_without_regeneration(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    source = notes._source(f.client.result, f.scope)
    with Session(f.engine) as session:
        transcript = AIVisitTranscript(
            doctor_id=f.case.doctor.id,
            patient_id=f.case.patient.id,
            appointment_id=f.case.appointment.id,
            duration=source.duration,
            transcript_data=source.entries,
        )
        session.add(transcript)
        session.flush()
        current = AIVisitNote(
            doctor_id=f.case.doctor.id,
            appointment_id=f.case.appointment.id,
            ai_transcript_id=transcript.id,
            doctor_note_schema_id=f.schema_ref,
            note={"CC": "Existing synthetic note", "Plan": "Keep existing text"},
        )
        session.add(current)
        session.commit()
        old_id = str(current.id)
    assert (
        await notes.capture_note(**{**kwargs, "prepare": False})
    ).status == "not_prepared"
    assert not rows(f, ScribeTranscriptDelivery)
    result = await notes.capture_note(**kwargs)
    assert result.fields is not None
    assert result.note_ref == old_id and result.fields["Plan"] == "Keep existing text"
    assert not f.model.calls
    assert (
        len(rows(f, ScribeTranscriptDelivery))
        == len(rows(f, AIVisitTranscript))
        == len(rows(f, AIVisitNote))
        == 1
    )


@pytest.mark.asyncio
async def test_template_section_changes_preserve_the_actual_existing_note(
    admission_case, monkeypatch
):
    from fastapi import Request

    from app.routes import note_template_fields as templates

    f = admission_case
    kwargs = await setup(f, monkeypatch)
    first = await notes.capture_note(**kwargs)
    monkeypatch.setattr(templates, "get_session", lambda: Session(f.engine))
    request = Request({"type": "http", "state": {"doctor": f.case.doctor}})
    before = templates.get_note_template_fields(request, f.schema_ref)
    templates.update_note_template_fields(
        request,
        f.schema_ref,
        templates.UpdateTemplateFields(
            expected_revision=before.revision,
            fields=[
                templates.TemplateField(
                    heading="Custom follow-up", guidance="Only discussed follow-up"
                )
            ],
        ),
    )
    current = await notes.capture_note(**{**kwargs, "prepare": False})
    assert current.note_ref == first.note_ref
    assert current.note_sha256 == first.note_sha256
    assert current.field_order == first.field_order
    assert current.fields == first.fields
    assert len(f.model.calls) == 1
    assert len(rows(f, AIVisitNote)) == 1


@pytest.mark.asyncio
async def test_legacy_delivery_keeps_default_pipeline_dispatch_on_replay(
    admission_case, monkeypatch
):
    original_dispatch = ingest._dispatch_pipeline_if_needed
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    await notes.capture_note(**kwargs)
    monkeypatch.setattr(ingest, "_dispatch_pipeline_if_needed", original_dispatch)
    sent = []
    monkeypatch.setattr(
        ingest.app.jobs.transcribe,
        "queue_schema_transcription",
        lambda _: sent.append("note"),
    )
    monkeypatch.setattr(
        ingest.app.jobs.score_engagement, "queue", lambda **_: sent.append("engagement")
    )
    source = notes._source(f.client.result, f.scope)
    arguments = {
        "encounter_id": f.case.appointment.id,
        "caller_client_id": f.caller.workload_client_id,
        "idempotency_key": "later-scribe-delivery",
        "scribe_session_id": f.scope.session_id,
        "payload_sha256": source.digest,
        "duration": source.duration,
        "transcript_data": source.entries,
    }
    assert ingest.perform(**arguments).disposition == "replayed"
    assert ingest.perform(**arguments).disposition == "replayed"
    assert sent == ["note", "engagement"]
    assert rows(f, ScribeTranscriptDelivery)[0].pipeline_dispatched_at is not None


@pytest.mark.parametrize("raw", ['{"CC":"\\ud800"}', '{"CC":"\\u0000"}'])
def test_invalid_unicode_never_reaches_note_persistence(raw):
    with pytest.raises(notes.CaptureNoteUnconfirmed):
        notes._validated_note(raw, {"CC": ""})


@pytest.mark.asyncio
async def test_templates_include_only_current_provider_active_schemas(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    with Session(f.engine) as session:
        from tests.authority_kernel.scribe_capture_scope_test import make_scope_case

        other = make_scope_case(session)
        foreign = DoctorNoteSchema(
            doctor_id=other.doctor.id,
            note_type="Foreign private template",
            fields={"Private": "Do not disclose"},
        )
        deleted = DoctorNoteSchema(
            doctor_id=f.case.doctor.id,
            note_type="Deleted template",
            fields={"Old": "Old"},
        )
        deleted.deleted_at = datetime.now(UTC)
        session.add_all([foreign, deleted])
        session.commit()
    template_args = {
        key: value
        for key, value in kwargs.items()
        if key not in {"schema_ref", "prepare", "model"}
    }
    receipt = await notes.capture_note_templates(**template_args)
    assert receipt.session_id == f.scope.session_id
    assert len(receipt.templates) == 1
    assert receipt.templates[0].schema_ref == f.schema_ref
    assert receipt.templates[0].field_names == ("CC", "Plan")
    assert "Foreign" not in receipt.model_dump_json()
    assert not f.model.calls and not rows(f, AIVisitTranscript)


@pytest.mark.asyncio
async def test_note_hash_changes_when_existing_core_editor_changes_same_record(
    admission_case, monkeypatch
):
    import hashlib

    import orjson

    f = admission_case
    kwargs = await setup(f, monkeypatch)
    first = await notes.capture_note(**kwargs)
    with Session(f.engine) as session:
        current = session.get(AIVisitNote, first.note_ref)
        assert current is not None
        current.note = {"CC": "Sleep", "Plan": "A revised synthetic plan"}
        session.add(current)
        session.commit()
    second = await notes.capture_note(**{**kwargs, "prepare": False})
    assert second.note_ref == first.note_ref
    assert second.note_sha256 != first.note_sha256
    assert second.source_sha256 == first.source_sha256
    assert second.fields is not None
    assert (
        second.note_sha256
        == hashlib.sha256(orjson.dumps(list(second.fields.items()))).hexdigest()
    )
    assert len(f.model.calls) == 1
