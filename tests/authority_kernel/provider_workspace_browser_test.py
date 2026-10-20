"""Opt-in local browser → Core facade → Scribe receiver, fictional data only."""

import asyncio
import json
import os
import socket
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

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
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_notes_test import FakeModel, rows
from tests.authority_kernel.scribe_configured_wire_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_configured_wire_test import (
    capture_engine as capture_engine,
)
from tests.authority_kernel.scribe_configured_wire_test import (
    configured_engine as configured_engine,
)
from tests.authority_kernel.scribe_creator_review_wire_test import wait_for_file
from tests.authority_kernel.scribe_gateway_receiver_test import client
from tests.services.scribe_workflow_test import StaticCredential

admission_case = _admission_case


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [390, 1280], ids=["mobile", "desktop"])
@pytest.mark.skipif(
    os.getenv("PROVIDER_WORKSPACE_BROWSER") != "1", reason="owned local browser harness"
)
async def test_provider_workspace_browser(admission_case, tmp_path, monkeypatch, width):
    assert str(tmp_path).startswith("/private/tmp/scribe-configured-core.provider.")
    root = Path(os.environ["SCRIBE_REVIEW_RECEIVER_ROOT"]).resolve()
    f = admission_case
    f.caller = replace(
        f.caller,
        capture_settings=replace(
            f.caller.capture_settings,
            notes_enabled=True,
            upload_origins=("https://storage.invalid",),
        ),
    )
    with Session(f.engine) as session:
        schema = DoctorNoteSchema(
            doctor_id=f.case.doctor.id,
            note_type="Synthetic SOAP",
            fields={"CC": "Chief complaint", "Plan": "Explicitly discussed plan"},
        )
        session.add(schema)
        session.commit()
    model = FakeModel()
    model.output = (
        '{"CC":"No fever reported.","Plan":"Invented medication instructions."}'
    )
    monkeypatch.setattr(notes, "ConfiguredNoteModel", lambda: model)
    path = tmp_path / "scope.json"
    path.write_text(json.dumps({"tenant_id": str(f.case.link.scribe_tenant_id)}))
    env = {
        **os.environ,
        "SCRIBE_PROVIDER_WIRE": "1",
        "SCRIBE_CONFIGURED_WIRE_SCOPE": str(path),
        "PYTHONPATH": str(root / "services/api/src"),
    }
    with (tmp_path / "receiver.log").open("wb") as log:
        receiver = await asyncio.create_subprocess_exec(
            os.environ["SCRIBE_PYTHON"],
            "-m",
            "pytest",
            "tests/test_provider_workspace_wire.py",
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
        server = serving = listener = browser = None
        calls = []
        try:
            await wait_for_file(path.with_suffix(".ready"), receiver)
            origin = f"http://127.0.0.1:{int(path.with_suffix('.ready').read_text())}"

            class LocalWire(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    assert request.url.host == "scribe.synthetic.invalid"
                    assert (
                        request.headers["authorization"] == "Bearer test-access-token"
                    )
                    async with httpx.AsyncClient(
                        base_url=origin, trust_env=False
                    ) as loopback:
                        return await loopback.request(
                            request.method,
                            request.url.raw_path.decode(),
                            headers=request.headers,
                            content=request.content,
                        )

            def remote():
                return HttpScribeWorkflowClient(
                    base_url="https://scribe.synthetic.invalid",
                    scope="api://scribe/.default",
                    credential=StaticCredential(),
                    timeout_seconds=10,
                    client=httpx.AsyncClient(
                        base_url="https://scribe.synthetic.invalid",
                        transport=LocalWire(),
                    ),
                )

            app = client().app
            assert isinstance(app, FastAPI)

            async def request_session():
                with Session(f.engine, expire_on_commit=False) as session:
                    token = _session_context.set(session)
                    try:
                        yield session
                    finally:
                        _session_context.reset(token)

            app.dependency_overrides[aglobal_session] = request_session
            app.dependency_overrides[get_admission_client_factory] = lambda: remote
            app.dependency_overrides[get_capture_settings] = (
                lambda: f.caller.capture_settings
            )
            app.dependency_overrides[get_capture_authority_settings] = (
                lambda: f.caller.settings
            )
            app.dependency_overrides[authenticate_scribe_capture_request] = (
                lambda: EntraServicePrincipal(client_id=f.caller.workload_client_id)
            )

            host = FastAPI()

            @host.get("/provider-fixture/context")
            async def context():
                return {
                    "scope": {
                        "workspaceId": str(f.binding.workspace_id),
                        "bindingRef": f.binding.binding_ref,
                        "appointmentRef": str(f.case.appointment.id),
                    },
                    "encounters": [
                        {
                            "appointmentRef": str(f.case.appointment.id),
                            "title": "Synthetic QA visit",
                            "detail": "No real patient",
                        }
                    ],
                }

            @host.post("/provider-fixture/core/{action}")
            async def proxy(action: str, request: Request):
                assert action in {
                    "capture-admissions",
                    "capture-operations",
                    "capture-notes",
                }
                body = await request.json()
                assert body["binding_ref"] == f.binding.binding_ref
                assert UUID(body["workspace_id"]) == f.binding.workspace_id
                key = request.headers["idempotency-key"]
                headers = {
                    "Idempotency-Key": key,
                    "X-Cosmaneura-Mount-Context": f.caller.mount_context_token,
                    "X-Cosmaneura-Interaction-Ref": f.caller.interaction_ref,
                    "X-Cosmaneura-Client-Turn-ID": str(f.caller.client_turn_id),
                    "X-Cosmaneura-Capture-Version": "1",
                }
                async with httpx.AsyncClient(
                    base_url="http://core.synthetic.invalid",
                    transport=httpx.ASGITransport(app),
                ) as facade:
                    result = await facade.post(
                        "/service/v1/agent/scribe/" + action, json=body, headers=headers
                    )
                calls.append((action, key, body, result.status_code))
                if (
                    action == "capture-operations"
                    and body["operation"]["kind"] == "complete"
                    and result.status_code == 200
                ):
                    scope = admission.authorize_admitted_capture(
                        f.caller, f.binding.binding_ref, f.binding.workspace_id
                    )
                    path.with_suffix(".process").write_text(
                        json.dumps(
                            {
                                "session_request": scope.session_request.model_dump(
                                    mode="json"
                                ),
                                "creator_actor_id": scope.creator_actor_id,
                            }
                        )
                    )
                    await wait_for_file(path.with_suffix(".processed"), receiver)
                return Response(
                    result.content,
                    status_code=result.status_code,
                    headers={
                        "Content-Type": "application/json",
                        "Cache-Control": "no-store",
                    },
                )

            @host.put("/provider-fixture/blob")
            async def blob(request: Request):
                name = request.query_params["path"].split("/")[-1].removesuffix(".enc")
                # The fixture may already have removed the eight-digit sequence.
                if len(name) == 45 and name[:8].isdigit() and name[8] == "-":
                    name = name[9:]
                object_id = UUID(name)
                assert any(
                    action == "capture-operations"
                    and body["operation"]["kind"] == "authorize_upload"
                    and body["operation"]["object_id"] == str(object_id)
                    and status == 200
                    for action, _, body, status in calls
                )
                async with httpx.AsyncClient(
                    base_url=origin, trust_env=False
                ) as loopback:
                    result = await loopback.put(
                        f"/__provider_blob/{object_id}", content=await request.body()
                    )
                return Response(status_code=result.status_code)

            @host.get("/provider-fixture/dashboard")
            async def dashboard():
                with Session(f.engine) as session:
                    current = [
                        row for row in rows(f, AIVisitNote) if row.superseded_at is None
                    ]
                    assert len(current) == 1
                    saved = session.get(AIVisitNote, current[0].id)
                    assert saved is not None
                    return JSONResponse({"fields": saved.note})

            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            server = uvicorn.Server(
                uvicorn.Config(host, log_level="critical", access_log=False)
            )
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started
            browser = await asyncio.create_subprocess_exec(
                os.environ["SCRIBE_NODE"],
                str(Path(__file__).parents[1] / "browser/provider-workspace.mjs"),
                str(root),
                f"http://127.0.0.1:{listener.getsockname()[1]}",
                str(tmp_path),
                str(width),
                env=os.environ,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(browser.communicate(), timeout=120)
            (tmp_path / "browser.stderr").write_bytes(stderr)
            (tmp_path / "request-statuses.json").write_text(
                json.dumps(
                    [
                        {
                            "route": action,
                            "kind": body.get("operation", {}).get("kind"),
                            "status": status,
                        }
                        for action, _, body, status in calls
                    ]
                )
            )
            assert browser.returncode == 0, (
                stderr.decode()[-4000:],
                [
                    (action, body.get("operation", {}).get("kind"), status)
                    for action, _, body, status in calls
                ],
            )
            assert json.loads(stdout)["saved_and_reopened"]
            assert len(rows(f, AIVisitTranscript)) == 1
            assert len(rows(f, AIVisitNote)) == 2
            current = [row for row in rows(f, AIVisitNote) if row.superseded_at is None]
            assert len(current) == 1
            assert current[0].note["CC"] == "Synthetic manual edit"
            assert current[0].note["Plan"] == "Not mentioned in transcript"
            assert len(model.calls) == 1
            assert any(c[0] == "capture-admissions" and c[3] == 200 for c in calls)
        finally:
            if browser is not None and browser.returncode is None:
                browser.kill()
                await browser.wait()
            if server is not None:
                assert serving is not None and listener is not None
                server.should_exit = True
                await serving
                listener.close()
            path.with_suffix(".done").touch()
            try:
                await asyncio.wait_for(receiver.wait(), timeout=15)
            except TimeoutError:
                receiver.terminate()
                await receiver.wait()
        assert receiver.returncode == 0, (tmp_path / "receiver.log").read_text()[-4000:]
        evidence = json.loads(path.with_suffix(".evidence.json").read_text())
        assert evidence == {
            "speech_calls": 1,
            "translation_calls": 1,
            "encrypted_chunks": 1,
            "decrypted_frames": 48000,
            "all_providers_offline": True,
        }
