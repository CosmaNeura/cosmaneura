"""Core routes → actual local Scribe → reviewed canonical Core SOAP."""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_scribe_capture_request,
)
from app.routes.scribe_capture import (
    get_admission_client_factory,
    get_capture_authority_settings,
    get_capture_settings,
)
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_notes as notes
from app.services.scribe_workflow import HttpScribeWorkflowClient

from activemodel.session_manager import _session_context, aglobal_session
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.scribe_transcript_delivery import ScribeTranscriptDelivery
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_admission_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_notes_test import FakeModel, rows
from tests.authority_kernel.scribe_configured_capture_test import (
    SELECTION,
)
from tests.authority_kernel.scribe_configured_capture_test import (
    capture_engine as capture_engine,
)
from tests.authority_kernel.scribe_configured_capture_test import (
    configured_engine as configured_engine,
)
from tests.authority_kernel.scribe_creator_review_test import decision_operation
from tests.authority_kernel.scribe_creator_review_wire_test import wait_for_file
from tests.authority_kernel.scribe_gateway_receiver_test import PATH, client
from tests.services.scribe_workflow_test import StaticCredential

ADMISSION_PATH = "/service/v1/agent/scribe/capture-admissions"
admission_case = _admission_case
NOTE_PATH = "/service/v1/agent/scribe/capture-notes"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["accepted", "rejected"])
async def test_configured_admission_review_and_existing_soap_routes(
    admission_case, tmp_path, monkeypatch, decision
):
    root = os.getenv("SCRIBE_REVIEW_RECEIVER_ROOT")
    if not root:
        pytest.skip("Explicit local Scribe checkout required")
    root = Path(root)
    assert str(tmp_path).startswith("/private/tmp/scribe-configured-core.")
    f = admission_case
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
        schema_ref = str(schema.id)
    model = FakeModel()
    monkeypatch.setattr(notes, "ConfiguredNoteModel", lambda: model)
    path = tmp_path / "scope.json"
    path.write_text(json.dumps({"tenant_id": str(f.case.link.scribe_tenant_id)}))
    env = {**os.environ, "SCRIBE_CONFIGURED_WIRE_SCOPE": str(path)}
    with (tmp_path / "receiver.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            str(root / ".venv/bin/python"),
            "-m",
            "pytest",
            "tests/test_core_configured_wire.py",
            "-q",
            "-o",
            "addopts=",
            f"--basetemp={tmp_path / 'receiver-temp'}",
            f"--junitxml={tmp_path / 'receiver.xml'}",
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            await wait_for_file(path.with_suffix(".ready"), process)
            origin = f"http://127.0.0.1:{int(path.with_suffix('.ready').read_text())}"
            posts = []
            lost = True
            async with httpx.AsyncClient(
                base_url=origin,
                trust_env=False,
                timeout=10,
                # Each capture route owns a private worker event loop.
                limits=httpx.Limits(max_keepalive_connections=0),
            ) as loopback:

                class LocalWire(httpx.AsyncBaseTransport):
                    async def handle_async_request(self, request):
                        nonlocal lost
                        assert request.url.host == "scribe.synthetic.invalid"
                        assert (
                            request.headers["authorization"]
                            == "Bearer test-access-token"
                        )
                        if request.url.path.startswith("/v1/captured-sessions"):
                            assert request.headers["x-scribe-creator-actor-id"] == str(
                                f.case.actor.id
                            )
                        response = await loopback.request(
                            request.method,
                            request.url.raw_path.decode(),
                            headers=request.headers,
                            content=request.content,
                        )
                        if request.url.path == "/v1/captured-sessions":
                            posts.append(
                                (request.content, request.headers["idempotency-key"])
                            )
                            if response.status_code == 201 and lost:
                                lost = False
                                raise httpx.ReadError(
                                    "synthetic lost admission reply", request=request
                                )
                        return response

                async with httpx.AsyncClient(
                    base_url="https://scribe.synthetic.invalid", transport=LocalWire()
                ) as transport:

                    def remote():
                        return HttpScribeWorkflowClient(
                            base_url="https://scribe.synthetic.invalid",
                            scope="api://scribe/.default",
                            credential=StaticCredential(),
                            timeout_seconds=10,
                            client=transport,
                        )

                    app = client().app
                    assert isinstance(app, FastAPI)

                    async def request_session():
                        # The request worker intentionally inherits no ambient session.
                        with Session(f.engine, expire_on_commit=False) as session:
                            token = _session_context.set(session)
                            try:
                                yield session
                            finally:
                                _session_context.reset(token)

                    app.dependency_overrides[aglobal_session] = request_session
                    app.dependency_overrides[get_admission_client_factory] = (
                        lambda: remote
                    )
                    assert isinstance(app, FastAPI)
                    app.dependency_overrides[get_capture_settings] = (
                        lambda: f.caller.capture_settings
                    )
                    assert isinstance(app, FastAPI)
                    app.dependency_overrides[get_capture_authority_settings] = (
                        lambda: f.caller.settings
                    )
                    assert isinstance(app, FastAPI)
                    app.dependency_overrides[authenticate_scribe_capture_request] = (
                        lambda: EntraServicePrincipal(
                            client_id=f.caller.workload_client_id
                        )
                    )
                    headers = {
                        "X-Cosmaneura-Mount-Context": f.caller.mount_context_token,
                        "X-Cosmaneura-Interaction-Ref": f.caller.interaction_ref,
                        "X-Cosmaneura-Client-Turn-ID": str(f.caller.client_turn_id),
                        "X-Cosmaneura-Capture-Version": "1",
                        "Idempotency-Key": "configured-native-" + uuid4().hex,
                        "X-Scribe-Creator-Actor-ID": "browser-assertion-must-be-ignored",
                    }
                    scope_body = dict(
                        binding_ref=f.binding.binding_ref,
                        workspace_id=str(f.binding.workspace_id),
                        request_nonce=str(uuid4()),
                    )
                    body = dict(
                        **scope_body,
                        attestation=f.attestation.model_dump(mode="json"),
                        configured_capture=SELECTION.model_dump(mode="json"),
                    )
                    async with httpx.AsyncClient(
                        base_url="http://core.synthetic.invalid",
                        transport=httpx.ASGITransport(app),
                    ) as browser:
                        first = await browser.post(
                            ADMISSION_PATH, json=body, headers=headers
                        )
                        assert first.status_code == 503, first.text
                        assert not lost, "Admission did not reach the injected lost reply"
                        path.with_suffix(".restart").touch()
                        await wait_for_file(path.with_suffix(".restarted"), process)
                        saved = await browser.post(
                            ADMISSION_PATH,
                            json={**body, "attestation": None},
                            headers=headers,
                        )
                        assert saved.status_code == 200, saved.text
                        assert posts[0] == posts[1]
                        scope = admission.authorize_admitted_capture(
                            f.caller, f.binding.binding_ref, f.binding.workspace_id
                        )
                        path.with_suffix(".capture").write_text(
                            json.dumps(
                                dict(
                                    session_request=scope.session_request.model_dump(
                                        mode="json"
                                    ),
                                    creator_actor_id=scope.creator_actor_id,
                                )
                            )
                        )
                        await wait_for_file(path.with_suffix(".captured"), process)
                        operation_body = dict(
                            **scope_body, operation={"kind": "creator_review"}
                        )
                        response = await browser.post(
                            PATH, json=operation_body, headers=headers
                        )
                        assert response.status_code == 200, response.text
                        before = response.json()["receipt"]
                        note_body = dict(
                            **scope_body,
                            expected_session_id=str(scope.session_id),
                            schema_ref=schema_ref,
                            action="prepare",
                        )
                        assert (
                            await browser.post(
                                NOTE_PATH, json=note_body, headers=headers
                            )
                        ).status_code == 409
                        assert not model.calls and not rows(f, AIVisitTranscript)
                        response = await browser.post(
                            PATH,
                            json={
                                **scope_body,
                                "operation": decision_operation(
                                    before, decision
                                ).model_dump(mode="json"),
                            },
                            headers=headers,
                        )
                        assert response.status_code == 200, response.text
                        current = (
                            await browser.post(
                                PATH, json=operation_body, headers=headers
                            )
                        ).json()["receipt"]
                        assert current["source"] == before["source"]
                        result = await browser.post(
                            NOTE_PATH, json=note_body, headers=headers
                        )
                        if decision == "rejected":
                            assert result.status_code == 409 and not model.calls
                            assert not rows(f, AIVisitTranscript) and not rows(
                                f, AIVisitNote
                            )
                        else:
                            assert result.status_code == 200, result.text
                            draft = result.json()["receipt"]
                            assert (
                                draft["review_required"]
                                and draft["reviewed_source"]["kind"]
                                == "reviewed_english"
                            )
                            recovered = await browser.post(
                                NOTE_PATH,
                                json={**note_body, "action": "read"},
                                headers=headers,
                            )
                            assert recovered.json()["receipt"] == draft
                            assert len(model.calls) == 1
                            assert all(
                                "No tengo fiebre" not in p for p, _ in model.calls
                            )
                            for phrase in (
                                "Any fever?",
                                "I do not have a fever.",
                                "I am not sure when it started.",
                            ):
                                assert all(phrase in p for p, _ in model.calls)
                            assert (
                                len(rows(f, AIVisitTranscript))
                                == len(rows(f, AIVisitNote))
                                == 1
                            )
                            assert (
                                rows(f, ScribeTranscriptDelivery)[
                                    0
                                ].pipeline_dispatched_at
                                is None
                            )
                        path.with_suffix(".revoke").touch()
                        await wait_for_file(path.with_suffix(".revoked"), process)
                        assert (
                            await browser.post(
                                NOTE_PATH,
                                json={**note_body, "action": "read"},
                                headers=headers,
                            )
                        ).status_code == 409
        finally:
            path.with_suffix(".done").touch()
            try:
                await asyncio.wait_for(process.wait(), timeout=15)
            except TimeoutError:
                process.terminate()
                await process.wait()
        assert process.returncode == 0, (tmp_path / "receiver.log").read_text()[-6000:]
