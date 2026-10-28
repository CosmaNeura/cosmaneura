"""New facts reads retain v1 artifacts and enforce current source authority."""

from typing import Protocol, cast

import pytest

import app.configuration.session_prep as configuration
import app.services.authority as authority
from app.configuration.session_prep import SessionPrepProviderSettings
from app.services.session_prep import (
    SessionPrepScopeDenied,
    authorize_session_prep_read,
)
from app.services.session_prep_note_sources import (
    finalize_note_for_session_prep,
    note_content_sha256,
)
from app.services.session_prep_runtime import (
    SessionPrepFactsToolResponse,
    invoke_provider_session_prep,
)

from activemodel.session_manager import get_session
from app.models.session_prep_runtime import SessionPrepArtifact, SessionPrepReadReceipt

from tests.services.session_prep_authority_test import AUTHORITY_SETTINGS
from tests.services.session_prep_runtime_test import (
    NOW,
    WORKLOAD,
    _action,
    _client,
    _read,
    _work,
)
from tests.services.session_prep_runtime_test import (
    scope as _base_scope,
)

scope = _base_scope


class _FactsInvocation(Protocol):
    response: SessionPrepFactsToolResponse
    replayed: bool


def _artifact() -> SessionPrepArtifact:
    value = SessionPrepArtifact.first()
    assert value is not None
    return value


def _receipt() -> SessionPrepReadReceipt:
    value = SessionPrepReadReceipt.first()
    assert value is not None
    return value


def _review(scope, content):
    doctor, _target, note, _token, _record = scope
    note.note = content
    note.save()
    with get_session() as session:
        finalize_note_for_session_prep(
            session=session,
            doctor_id=doctor.id,
            note_id=note.id,
            expected_content_sha256=note_content_sha256(note.note),
            observed_at=NOW,
        )
        session.commit()


def _facts_read(
    token, record, action, *, key="facts-read-key-000001", context=None
) -> _FactsInvocation:
    client_turn_id, interaction_ref, turn_ref = action
    with get_session() as session:
        authorized = authorize_session_prep_read(
            session=session,
            workload_client_id=WORKLOAD,
            mount_context_token=token,
            interaction_ref=interaction_ref,
            client_turn_id=client_turn_id,
            source_turn_ref=turn_ref,
            settings=AUTHORITY_SETTINGS,
        )
        result = invoke_provider_session_prep(
            session=session,
            authorization=authorized,
            workload_client_id=WORKLOAD,
            idempotency_key=key,
            source_turn_ref=turn_ref,
            context_snapshot_ref=context or record.context_snapshot_ref,
            tool_version="1.1.0",
            detail="structured_facts",
            observed_at=NOW,
            settings=AUTHORITY_SETTINGS,
        )
        session.commit()
        assert isinstance(result.response, SessionPrepFactsToolResponse)
        return cast(_FactsInvocation, result)


def test_facts_are_available_while_brief_is_pending_without_a_model_call(scope):
    _review(scope, {"Objective": "Office BP: 148/92 mmHg."})
    _doctor, _target, _note, token, record = scope
    action = _action(token)
    read = _facts_read(token, record, action)
    result = read.response.result
    assert read.response.tool_version == "1.1.0"
    assert result.schema_version == "1.1.0"
    assert result.preparation.state == "pending"
    assert result.facts_state == "ready"
    assert result.structured_facts is not None
    assert result.structured_facts.facts[0].value == "148/92 mmHg"
    assert SessionPrepArtifact.count() == 0
    assert SessionPrepReadReceipt.count() == 1
    replay = _facts_read(token, record, action)
    assert replay.replayed and replay.response == read.response


def test_v1_artifact_and_response_shape_are_not_extended_or_replaced(scope):
    _review(scope, {"Medications": "Taking losartan 25 mg daily."})
    _doctor, _target, _note, token, record = scope
    action = _action(token)
    _read(token, record, action)
    assert _work() == "ready"
    artifact = _artifact()
    before = dict(artifact.result_payload)
    original = _read(token, record, action, key="v1-read-after-build-0001")
    facts = _facts_read(token, record, action)
    assert facts.response.result.preparation == original.response.result
    assert original.response.result.schema_version == "1.0.0"
    assert "structured_facts" not in original.response.model_dump_json()
    assert _artifact().result_payload == before
    assert SessionPrepArtifact.count() == 1
    assert facts.response.result.structured_facts is not None
    assert facts.response.result.structured_facts.facts[0].state == "documented_taking"


def test_pending_receipt_cannot_replay_facts_after_same_note_correction(scope):
    _review(scope, {"Objective": "Office BP: 148/92 mmHg."})
    _doctor, _target, note, token, record = scope
    action = _action(token)
    _facts_read(token, record, action)
    note.note = {"Objective": "Office BP: 126/80 mmHg."}
    note.save()
    with pytest.raises(SessionPrepScopeDenied):
        _facts_read(token, record, action)
    no_sources = _facts_read(token, record, _action(token), key="new-facts-read-000001")
    assert no_sources.response.result.facts_state == "unavailable"
    assert no_sources.response.result.structured_facts is None
    assert "148/92" not in no_sources.response.model_dump_json()
    _review(scope, note.note)
    corrected = _facts_read(
        token, record, _action(token), key="corrected-facts-read-00001"
    )
    assert corrected.response.result.structured_facts is not None
    assert corrected.response.result.structured_facts.facts[0].value == "126/80 mmHg"
    assert (
        corrected.response.result.structured_facts.facts[0].evidence.source_version == 3
    )


def test_forged_record_handle_and_provider_revocation_deny_facts(scope, monkeypatch):
    _review(scope, {"Objective": "Office BP: 148/92 mmHg."})
    _doctor, _target, _note, token, record = scope
    action = _action(token)
    with pytest.raises(SessionPrepScopeDenied):
        _facts_read(token, record, action, context="context_forged_facts_00001")
    _facts_read(token, record, action)
    monkeypatch.setattr(
        configuration, "SESSION_PREP_PROVIDER_SETTINGS", SessionPrepProviderSettings()
    )
    with pytest.raises((SessionPrepScopeDenied, authority.AuthorityRequestDeniedError)):
        _facts_read(token, record, action)


@pytest.mark.parametrize(
    "version,detail,status",
    [
        ("1.1.0", "structured_facts", 200),
        ("1.0.0", "structured_facts", 422),
        ("1.1.0", "brief", 422),
        ("1.9.0", "structured_facts", 404),
    ],
)
def test_real_receiver_version_is_explicit_and_commits_before_egress(
    scope, version, detail, status
):
    _review(scope, {"Objective": "Office BP: 148/92 mmHg."})
    _doctor, _target, _note, token, record = scope
    client_turn_id, interaction_ref, turn_ref = _action(token)
    response = _client().post(
        "/service/v1/agent/tools/invoke",
        headers={
            "X-Cosmaneura-Mount-Context": token,
            "X-Cosmaneura-Interaction-Ref": interaction_ref,
            "X-Cosmaneura-Client-Turn-ID": str(client_turn_id),
            "Idempotency-Key": "api-facts-read-000001",
        },
        json={
            "tool_name": "session-prep.context.read",
            "tool_version": version,
            "subject_ref": None,
            "arguments": {
                "source_turn_ref": turn_ref,
                "context_snapshot_ref": record.context_snapshot_ref,
                "purpose": "session_prep",
                "detail": detail,
            },
        },
    )
    assert response.status_code == status
    if status == 200:
        assert response.headers["Cache-Control"] == "no-store"
        assert (
            response.json()["result"]["structured_facts"]["facts"][0]["value"]
            == "148/92 mmHg"
        )
        assert SessionPrepReadReceipt.count() == 1
    else:
        assert SessionPrepReadReceipt.count() == 0


def test_old_unsafe_classification_receipt_cannot_survive_current_context_validation(
    scope, monkeypatch
):
    import app.services.session_prep_runtime as runtime
    from app.services.session_prep_facts import (
        DocumentedFact,
        FactSource,
        SourceEvidence,
        StructuredFactSet,
    )

    _review(scope, {"Medications": "Taking losartan 25 mg; for a neighbor."})
    _doctor, _target, _note, token, record = scope
    action = _action(token)

    def older_projection(selection):
        source = selection.sources[0]
        lineage = dict(
            source_ref=source.source_ref,
            source_version=source.source_version,
            source_at=source.source_at,
        )
        quote = "Taking losartan 25 mg"
        return StructuredFactSet(
            source_count=1,
            selection_limited=False,
            facts_limited=False,
            sources=[FactSource(**lineage)],
            facts=[
                DocumentedFact(
                    fact_ref="fact_unsafe_prior_parser_0001",
                    kind="medication",
                    subject="losartan",
                    value="25 mg",
                    state="documented_taking",
                    setting=None,
                    evidence=SourceEvidence(
                        **lineage,
                        section="Medications",
                        quote=quote,
                        start=0,
                        end=len(quote),
                    ),
                )
            ],
        )

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "project_explicit_facts", older_projection)
        old = _facts_read(token, record, action)
    before = dict(_receipt().response_payload)
    assert old.response.result.structured_facts is not None
    assert old.response.result.structured_facts.facts[0].state == "documented_taking"
    with pytest.raises(SessionPrepScopeDenied):
        _facts_read(token, record, action)
    assert _receipt().response_payload == before
    current = _facts_read(
        token, record, _action(token), key="current-context-read-0001"
    )
    assert current.response.result.structured_facts is not None
    assert current.response.result.structured_facts.facts == []
