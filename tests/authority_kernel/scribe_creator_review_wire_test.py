"""Actual Core route → loopback HTTP → Scribe receiver, separate synthetic DBs."""

import asyncio
import json
import os
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
from app.services.scribe_workflow import HttpScribeWorkflowClient

from activemodel.session_manager import _session_context, aglobal_session
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_admission_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_creator_review_authority_test import admitted
from tests.authority_kernel.scribe_creator_review_authority_test import (
    capture_engine as capture_engine,
)
from tests.authority_kernel.scribe_creator_review_authority_test import (
    review_engine as review_engine,
)
from tests.authority_kernel.scribe_creator_review_test import decision_operation
from tests.authority_kernel.scribe_gateway_receiver_test import PATH, client
from tests.services.scribe_workflow_test import StaticCredential

admission_case = _admission_case


async def wait_for_file(path, process):
    for _ in range(400):
        if path.exists():
            return
        if process.returncode is not None:
            pytest.fail("Scribe receiver fixture exited; inspect retained receiver.log")
        await asyncio.sleep(0.1)
    pytest.fail("Scribe receiver fixture did not become ready")


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["accepted", "rejected"])
async def test_core_to_scribe_review_and_lost_reply(admission_case, tmp_path, decision):
    root = os.getenv("SCRIBE_REVIEW_RECEIVER_ROOT")
    if not root:
        pytest.skip("Explicit local Scribe checkout required")
    root = Path(root)
    assert (root / "tests/test_core_creator_review_wire.py").is_file()
    assert str(tmp_path).startswith("/private/tmp/scribe-core-review.")
    f = admission_case
    scope = await admitted(f)
    path = tmp_path / "scope.json"
    path.write_text(
        json.dumps(
            dict(
                tenant_id=str(scope.tenant_id),
                session_id=str(scope.session_id),
                creator_actor_id=scope.creator_actor_id,
                session_request=scope.session_request.model_dump(mode="json"),
            )
        )
    )
    env = {**os.environ, "SCRIBE_CORE_WIRE_SCOPE": str(path)}
    with (tmp_path / "receiver.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            str(root / ".venv/bin/python"),
            "-m",
            "pytest",
            "tests/test_core_creator_review_wire.py",
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
            calls = []
            lose_reply = True

            async with httpx.AsyncClient(
                base_url=origin, trust_env=False, timeout=10
            ) as loopback:
                review_path = f"/v1/captured-sessions/{scope.session_id}/review"
                trusted = {
                    "X-Tenant-ID": str(scope.tenant_id),
                    "X-Scribe-Creator-Actor-ID": scope.creator_actor_id,
                    "Authorization": "Bearer test-access-token",
                }
                for foreign in (
                    {"X-Tenant-ID": str(uuid4())},
                    {"X-Scribe-Creator-Actor-ID": "unmapped-creator"},
                ):
                    denied = await loopback.get(
                        review_path, headers={**trusted, **foreign}
                    )
                    assert (
                        denied.status_code == 404
                        and denied.headers["cache-control"] == "no-store"
                    )

                class LocalWire(httpx.AsyncBaseTransport):
                    async def handle_async_request(self, request):
                        nonlocal lose_reply
                        assert request.url.host == "scribe.synthetic.invalid"
                        assert (
                            request.headers["authorization"]
                            == "Bearer test-access-token"
                        )
                        assert (
                            request.headers["x-scribe-creator-actor-id"]
                            == scope.creator_actor_id
                        )
                        calls.append(
                            (request.method, dict(request.headers), request.content)
                        )
                        # The facade runs in a private worker event loop. The
                        # probe client above already owns connections on the
                        # test loop, so it cannot also serve worker requests.
                        async with httpx.AsyncClient(
                            base_url=origin, trust_env=False, timeout=10
                        ) as receiver:
                            response = await receiver.request(
                                request.method,
                                request.url.raw_path.decode(),
                                headers=request.headers,
                                content=request.content,
                            )
                        if (
                            request.method == "POST"
                            and response.status_code == 200
                            and lose_reply
                        ):
                            lose_reply = False
                            raise httpx.ReadError(
                                "synthetic lost committed reply", request=request
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
                        # Capture workers inherit no ambient session. Own the
                        # synthetic database session inside this request.
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
                        "Idempotency-Key": "native-review-" + uuid4().hex,
                        "X-Scribe-Creator-Actor-ID": "browser-assertion-must-be-ignored",
                    }
                    body = dict(
                        binding_ref=f.binding.binding_ref,
                        workspace_id=str(f.binding.workspace_id),
                        request_nonce=str(uuid4()),
                        operation=dict(kind="creator_review"),
                    )
                    async with httpx.AsyncClient(
                        base_url="http://core.synthetic.invalid",
                        transport=httpx.ASGITransport(app),
                    ) as browser:
                        view_response = await browser.post(
                            PATH, json=body, headers=headers
                        )
                        assert view_response.status_code == 200, view_response.text
                        assert view_response.headers["cache-control"] == "no-store"
                        view = view_response.json()["receipt"]
                        op = decision_operation(view, decision)
                        decision_body = {
                            **body,
                            "operation": op.model_dump(mode="json"),
                        }
                        # Current-source mismatch conflicts before a review can commit.
                        stale = op.model_dump(mode="json")
                        stale["payload"]["expected_source_transcript_version"] += 1
                        response = await browser.post(
                            PATH, json={**body, "operation": stale}, headers=headers
                        )
                        assert response.status_code == 409
                        lost = await browser.post(
                            PATH, json=decision_body, headers=headers
                        )
                        assert lost.status_code == 503
                        # Fresh Scribe runtime and fresh Core route/client, same exact request/key.
                        path.with_suffix(".restart").touch()
                        await wait_for_file(path.with_suffix(".restarted"), process)
                        saved = await browser.post(
                            PATH, json=decision_body, headers=headers
                        )
                        assert saved.status_code == 200, saved.text
                        repeated = await browser.post(
                            PATH, json=decision_body, headers=headers
                        )
                        assert repeated.json()["receipt"] == saved.json()["receipt"]
                        posts = [c for c in calls if c[0] == "POST"]
                        assert posts[-3:] == [posts[-1]] * 3
                        assert (
                            posts[-1][1]["idempotency-key"]
                            == headers["Idempotency-Key"]
                        )
                        current = (
                            await browser.post(PATH, json=body, headers=headers)
                        ).json()["receipt"]
                        assert current["source"] == view["source"]
                        assert current["working"]["note_ready"] == (
                            decision == "accepted"
                        )
                        assert current["status"] == (
                            "transcript_ready"
                            if decision == "accepted"
                            else "translation_pending"
                        )
                        path.with_suffix(".revoke").touch()
                        await wait_for_file(path.with_suffix(".revoked"), process)
                        assert (
                            await browser.post(
                                PATH, json=decision_body, headers=headers
                            )
                        ).status_code == 409
                        assert (
                            await browser.post(PATH, json=body, headers=headers)
                        ).status_code == 409
        finally:
            path.with_suffix(".done").touch()
            try:
                await asyncio.wait_for(process.wait(), timeout=15)
            except TimeoutError:
                process.terminate()
                await process.wait()
        assert process.returncode == 0, (tmp_path / "receiver.log").read_text()[-6000:]
