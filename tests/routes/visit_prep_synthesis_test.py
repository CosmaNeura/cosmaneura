"""Real owner-only HTTP + source/queue/artifact/ledger, synthetic model only."""

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi import Request

import app.routes.visit_prep_synthesis as route
from app.routes.dependencies.view_as import apply_view_as
from app.services import visit_prep_access
from app.services.visit_prep_interviews import (
    complete_interview,
    confirm_interview,
    report_wrong_recipient,
)
from app.services.visit_prep_synthesis_builds import (
    claim_visit_prep_synthesis,
    fail_visit_prep_synthesis_claim,
)
from app.services.visit_prep_synthesis_contract import VisitPrepSynthesisInvalid
from app.services.visit_prep_synthesis_worker import run_one_build

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.doctor import Doctor
from sqlmodel import select

from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_interviews_test import _fixture, _respond
from tests.services.visit_prep_synthesis_builds_test import _build
from tests.services.visit_prep_synthesis_publication_test import _rows, _selected
from tests.services.visit_prep_synthesis_test import _brief, _fake
from tests.services.visit_prep_synthesis_usage_test import _events
from tests.services.visit_preps_test import _withdraw_seat


def _path(fixture):
    return f"/internal/v1/visit-prep/preps/{fixture.prep_id}/synthesis"


def _private(response, fixture):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert fixture.access_token not in response.text
    assert "ciphertext" not in response.text and "requester" not in response.text


@pytest.mark.parametrize("role,complete", [("patient", True), ("caregiver", False)])
def test_real_http_request_worker_and_current_cited_brief_without_chart(
    monkeypatch, role, complete
):
    fixture = _prepared(monkeypatch, "synthesis-http", role=role, complete=complete)
    selected = _selected(fixture)
    captured = _fake(monkeypatch, _brief(selected).model_dump_json())
    with _provider_client(fixture.doctor, monkeypatch) as client:
        missing = client.get(_path(fixture))
        assert missing.status_code == 200
        body = missing.json()
        assert body["contract"] == "visit_prep.patient_synthesis.v1"
        assert body["status"] == "missing" and body["build_status"] == "not_requested"
        assert body["brief"] is body["artifact_id"] is body["version"] is None
        assert body["sources"]["actor_role"] == role
        assert body["sources"]["preparation"]["patient_id"] is None
        assert body["sources"]["preparation"]["appointment_id"] is None
        assert body["sources"]["current_answers_complete"] == complete
        assert "synthesis_status" not in body["sources"]
        assert not captured and not _events(fixture)
        queued = client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        assert queued.status_code == 202 and queued.json()["state"] == "pending"
        assert set(queued.json()) == {"package_id", "source_version", "state"}
        before = _build(fixture)
        replay = client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        after = _build(fixture)
        assert replay.json() == queued.json()
        assert (before.epoch, before.attempts, before.retry_at) == (
            after.epoch,
            after.attempts,
            after.retry_at,
        )
        assert not captured and not _events(fixture) and not _rows(fixture)
        pending = client.get(_path(fixture))
        assert pending.json()["build_status"] == "pending"
        assert run_one_build() == "ready"
        ready = client.get(_path(fixture))
        body = ready.json()
        assert ready.status_code == 200
        assert body["status"] == body["build_status"] == "ready"
        assert body["brief"] == _brief(selected).model_dump()
        assert body["sources"]["source_version"] == selected.source_version
        assert body["version"] == 1 and body["artifact_id"] and body["recorded_at"]
        assert body["retry_at"] is None
        assert len(_events(fixture)) == 2 and len(_rows(fixture)) == 1
        reuse = client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        assert reuse.status_code == 200 and reuse.json()["state"] == "ready"
        assert run_one_build() == "idle" and len(_events(fixture)) == 2
    for response in (missing, queued, replay, pending, ready, reuse):
        _private(response, fixture)
    with get_session() as session:
        audits = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                AuthorityAuditEvent.event_type == "visit_prep.synthesis_read",
            )
        ).all()
    assert len(audits) == 3
    assert all(audit.request_digest == selected.source_version for audit in audits)


@pytest.mark.parametrize("change", ["correction", "review"])
def test_source_change_never_reads_old_brief_and_rejects_old_request(
    monkeypatch, change
):
    fixture = _prepared(monkeypatch, "synthesis-http-stale", adherence="some")
    selected = _selected(fixture)
    _fake(monkeypatch, _brief(selected).model_dump_json())
    with _provider_client(fixture.doctor, monkeypatch) as client:
        client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        assert run_one_build() == "ready"
        old = client.get(_path(fixture)).json()
        ciphertext = _rows(fixture)[0].payload_ciphertext
        if change == "correction":
            _correct(fixture, question="adherence", answer="yes")
        else:
            _review(fixture)
        current = _selected(fixture)
        assert current.source_version != selected.source_version
        from app.services import visit_prep_synthesis_publication as publication

        original = publication.decrypt_synthesis_payload
        monkeypatch.setattr(
            publication,
            "decrypt_synthesis_payload",
            lambda *_args: pytest.fail("old-source output must not be decrypted"),
        )
        stale = client.get(_path(fixture))
        body = stale.json()
        assert body["status"] == "stale" and body["build_status"] == "not_requested"
        assert body["brief"] is body["artifact_id"] is body["version"] is None
        assert body["sources"]["source_version"] == current.source_version
        if change == "correction":
            assert "adherence_barrier" not in stale.text
            assert "An invented barrier." not in stale.text
        denied = client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        assert denied.status_code == 409
        assert _build(fixture).source_version == selected.source_version
        monkeypatch.setattr(publication, "decrypt_synthesis_payload", original)
        _fake(monkeypatch, _brief(current).model_dump_json())
        client.post(
            _path(fixture), json={"expected_source_version": current.source_version}
        )
        assert run_one_build() == "ready"
        new = client.get(_path(fixture)).json()
        assert new["version"] == old["version"] + 1
        assert new["sources"]["source_version"] == current.source_version
    assert _rows(fixture)[0].payload_ciphertext == ciphertext
    assert len(_rows(fixture)) == 2 and len(_events(fixture)) == 4


def test_running_backoff_and_terminal_failure_are_read_only_not_new_requests(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "synthesis-http-failure")
    selected = _selected(fixture)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        claim = claim_visit_prep_synthesis()
        running = client.get(_path(fixture)).json()
        assert running["build_status"] == "running" and running["brief"] is None
        assert fail_visit_prep_synthesis_claim(claim, code="generation_failed")
        backoff = _build(fixture)
        pending = client.get(_path(fixture)).json()
        assert pending["build_status"] == "pending" and pending["retry_at"]
        client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        assert _build(fixture).retry_at == backoff.retry_at
        assert _build(fixture).attempts == 1 and not _events(fixture)
        from app.services import visit_prep_synthesis_builds as builds

        original = builds._database_now
        monkeypatch.setattr(
            builds,
            "_database_now",
            lambda _session: backoff.retry_at + timedelta(seconds=1),
        )
        next_claim = claim_visit_prep_synthesis()
        assert fail_visit_prep_synthesis_claim(next_claim, code="usage_unavailable")
        monkeypatch.setattr(builds, "_database_now", original)
        failure = client.get(_path(fixture))
        assert (
            failure.json()["build_status"] == "failed"
            and failure.json()["brief"] is None
        )
        same = client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        assert same.status_code == 200 and same.json()["state"] == "failed"
        assert _build(fixture).attempts == 2 and not _rows(fixture)
    _private(failure, fixture)


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    "denial",
    ["support", "gate", "baa", "seat", "identity", "quarantine", "other_owner"],
)
def test_current_http_authority_denial_prevents_read_and_queue(
    monkeypatch, method, denial
):
    fixture = _prepared(monkeypatch, "synthesis-http-denial")
    selected = _selected(fixture)
    doctor = fixture.doctor
    if denial == "baa":
        fixture.practice.negotiated_baa_on_file = False
        fixture.practice.save()
    elif denial == "seat":
        _withdraw_seat(fixture.owner)
    elif denial == "identity":
        with get_session() as session:
            current = session.get(Doctor, doctor.id)
            current.clerk_id = "user_synthetic_changed_identity"
            session.add(current)
            session.commit()
    elif denial == "quarantine":
        report_wrong_recipient(access_token=fixture.access_token)
    elif denial == "other_owner":
        doctor = _prepared(monkeypatch, "synthesis-http-other").doctor
    with _provider_client(doctor, monkeypatch) as client:
        if denial == "support":

            def support(request: Request):
                request.state.view_as = object()

            client.app.dependency_overrides[apply_view_as] = support
        if denial == "gate":
            monkeypatch.setattr(
                visit_prep_access,
                "VISIT_PREP_SETTINGS",
                replace(visit_prep_access.VISIT_PREP_SETTINGS, enabled=False),
            )
        response = client.request(
            method,
            _path(fixture),
            **(
                {"json": {"expected_source_version": selected.source_version}}
                if method == "POST"
                else {}
            ),
        )
    assert response.status_code == 404 and response.json() == {"detail": "Not found"}
    assert (
        "invented" not in response.text and not _rows(fixture) and not _events(fixture)
    )
    with get_session() as session:
        from app.models.visit_prep_synthesis_build import VisitPrepSynthesisBuild

        assert not session.exec(
            select(VisitPrepSynthesisBuild).where(
                VisitPrepSynthesisBuild.visit_prep_id == fixture.prep_id
            )
        ).all()
    _private(response, fixture)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"expected_source_version": 123},
        {"expected_source_version": "a" * 63},
        {"expected_source_version": "a" * 64, "brief": "Synthetic injected output"},
        {"expected_source_version": "a" * 64, "owner_doctor_id": "doctor_synthetic"},
    ],
)
def test_requests_accept_only_strict_source_hash_without_input_echo(
    monkeypatch, payload
):
    fixture = _prepared(monkeypatch, "synthesis-http-invalid")
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.post(_path(fixture), json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "Visit Prep request is invalid."}
    _private(response, fixture)


@pytest.mark.parametrize("declared", [None, "1"])
def test_actual_stream_limit_before_parsing_or_queue_even_with_false_length(
    monkeypatch, declared
):
    fixture = _prepared(monkeypatch, "synthesis-http-size")
    monkeypatch.setattr(
        route,
        "request_visit_prep_synthesis",
        lambda **_: pytest.fail("oversize request reached DB"),
    )
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.post(
            _path(fixture),
            content=iter([b" " * 700, b" " * 700]),
            headers={
                "Content-Type": "application/json",
                **({"Content-Length": declared} if declared else {}),
            },
        )
    assert response.status_code == 413
    _private(response, fixture)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unknown_queries_and_malformed_paths_are_private(monkeypatch, method):
    fixture = _prepared(monkeypatch, "synthesis-http-query")
    with _provider_client(fixture.doctor, monkeypatch) as client:
        for path in (
            _path(fixture) + "?access_token=vp1_synthetic",
            "/internal/v1/visit-prep/preps/not-a-prep/synthesis",
        ):
            response = client.request(
                method,
                path,
                **(
                    {"json": {"expected_source_version": "a" * 64}}
                    if method == "POST"
                    else {}
                ),
            )
            assert response.status_code in {404, 422}
            assert (
                "vp1_synthetic" not in response.text
                and "not-a-prep" not in response.text
            )
            _private(response, fixture)


def test_invalid_retained_output_maps_to_sanitized_unavailable_without_traceback(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "synthesis-http-corrupt")

    def broken(**_kwargs):
        raise VisitPrepSynthesisInvalid()

    monkeypatch.setattr(route, "get_visit_prep_synthesis", broken)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.get(_path(fixture))
    assert response.status_code == 503
    assert response.json() == {"detail": "Visit Prep is temporarily unavailable."}
    _private(response, fixture)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_sql_or_encryption_failure_is_private_and_never_returns_a_result(
    monkeypatch, method
):
    fixture = _prepared(monkeypatch, "synthesis-http-persistence-error")
    selected = _selected(fixture)
    from app.utils.encryption import EncryptionError

    from sqlalchemy.exc import SQLAlchemyError

    def unavailable(**_kwargs):
        raise (
            SQLAlchemyError("Synthetic private parameter")
            if method == "GET"
            else EncryptionError("Synthetic private parameter")
        )

    monkeypatch.setattr(
        route,
        "get_visit_prep_synthesis"
        if method == "GET"
        else "request_visit_prep_synthesis",
        unavailable,
    )
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.request(
            method,
            _path(fixture),
            **(
                {"json": {"expected_source_version": selected.source_version}}
                if method == "POST"
                else {}
            ),
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "Visit Prep is temporarily unavailable."}
    assert "Synthetic private parameter" not in response.text
    _private(response, fixture)


def test_operations_are_named_internal_and_preserve_non_disclosing_access_gate():
    from app.routes.dependencies.practice_gate import EXEMPT_ROUTE_NAMES
    from app.routes.internal import internal_api_app
    from app.utils.openapi import generate_openapi_schema

    schema = generate_openapi_schema(internal_api_app)
    path = "/internal/v1/visit-prep/preps/{prep_id}/synthesis"
    assert schema["paths"][path]["get"]["operationId"] == "visit_prep_synthesis"
    assert (
        schema["paths"][path]["post"]["operationId"] == "visit_prep_synthesis_request"
    )
    assert {
        "visit_prep_synthesis",
        "visit_prep_synthesis_request",
    } <= EXEMPT_ROUTE_NAMES
    assert (
        schema["components"]["schemas"]["VisitPrepSynthesisRequest"][
            "additionalProperties"
        ]
        is False
    )
    assert not any(
        path.startswith("/external/") and "synthesis" in path
        for path in schema["paths"]
    )


def test_all_skipped_http_package_gets_empty_brief_without_sdk_or_usage(monkeypatch):
    fixture = _fixture(monkeypatch, "synthesis-http-skipped")
    confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    for question in ("reason", "medications", "adherence", "story"):
        _respond(fixture, question)
    complete_interview(access_token=fixture.access_token)
    selected = _selected(fixture)
    from app.services import visit_prep_synthesis_generator as generator

    monkeypatch.setattr(generator, "openai", None)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        queued = client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        assert queued.status_code == 202
        assert run_one_build() == "ready"
        result = client.get(_path(fixture))
    assert result.status_code == 200
    assert result.json()["brief"] == _brief(selected).model_dump()
    assert result.json()["sources"]["skipped_question_keys"] == [
        "reason",
        "medications",
        "adherence",
        "story",
    ]
    assert not _events(fixture) and len(_rows(fixture)) == 1
    _private(result, fixture)


def test_ready_queue_without_artifact_is_not_claimed_as_ready(monkeypatch):
    fixture = _prepared(monkeypatch, "synthesis-http-no-artifact")
    selected = _selected(fixture)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        with get_session() as session:
            from app.models.visit_prep_synthesis_build import VisitPrepSynthesisBuild

            build = session.get(VisitPrepSynthesisBuild, _build(fixture).id)
            build.state = "ready"
            session.add(build)
            session.commit()
        response = client.get(_path(fixture))
        assert response.json()["status"] == "missing"
        assert (
            response.json()["build_status"] == "failed"
            and response.json()["brief"] is None
        )
        # Only an explicit current-source request repairs the inconsistency.
        requested = client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        assert requested.status_code == 202 and requested.json()["state"] == "pending"
        assert _build(fixture).epoch == 2


def test_http_read_denies_authority_expired_during_audit_flush(monkeypatch):
    fixture = _prepared(monkeypatch, "synthesis-http-expired-audit")
    selected = _selected(fixture)
    _fake(monkeypatch, _brief(selected).model_dump_json())
    with _provider_client(fixture.doctor, monkeypatch) as client:
        client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        assert run_one_build() == "ready"
        from app.services import visit_prep_synthesis_publication as publication

        original = publication._audit
        future = fixture.scheduled_at + timedelta(days=1)
        fixture.practice.billing_exempt_until = future - timedelta(seconds=1)
        fixture.practice.save()

        def expired_after_staging(*args, **kwargs):
            original(*args, **kwargs)
            monkeypatch.setattr(publication, "_database_now", lambda _session: future)

        monkeypatch.setattr(publication, "_audit", expired_after_staging)
        response = client.get(_path(fixture))
    assert response.status_code == 404 and response.json() == {"detail": "Not found"}
    assert "invented" not in response.text
    with get_session() as session:
        assert not session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                AuthorityAuditEvent.event_type == "visit_prep.synthesis_read",
            )
        ).all()
    _private(response, fixture)
