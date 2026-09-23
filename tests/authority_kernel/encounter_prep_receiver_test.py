"""Closed read facade; existing workload/interaction checks stay authoritative."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException

from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_scribe_capture_request,
)
from app.routes.scribe_capture import (
    get_capture_authority_settings,
    get_capture_settings,
)
from app.services import encounter_prep

from tests.authority_kernel.encounter_prep_test import admission_case as _admission_case
from tests.authority_kernel.encounter_prep_test import capture_engine as _capture_engine
from tests.authority_kernel.encounter_prep_test import intake
from tests.authority_kernel.scribe_capture_receiver_test import client, headers

admission_case = _admission_case
capture_engine = _capture_engine
PATH = "/service/v1/agent/scribe/encounter-context"


def test_real_scoped_read_on_the_facade_and_os_client(admission_case):
    f = admission_case
    intake(f)
    connection = client()
    assert isinstance(connection.app, FastAPI)
    connection.app.dependency_overrides[get_capture_settings] = lambda: replace(
        f.caller.capture_settings, prep_enabled=True
    )
    connection.app.dependency_overrides[get_capture_authority_settings] = (
        lambda: f.caller.settings
    )
    connection.app.dependency_overrides[authenticate_scribe_capture_request] = (
        lambda: EntraServicePrincipal(client_id=f.caller.workload_client_id)
    )
    payload = {
        "binding_ref": f.binding.binding_ref,
        "workspace_id": str(f.binding.workspace_id),
        "request_nonce": str(uuid4()),
    }
    signed = {
        **headers(),
        "X-Cosmaneura-Mount-Context": f.caller.mount_context_token,
        "X-Cosmaneura-Interaction-Ref": f.caller.interaction_ref,
        "X-Cosmaneura-Client-Turn-ID": str(f.caller.client_turn_id),
    }
    response = connection.post(PATH, json=payload, headers=signed)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert (
        response.json()["receipt"]["sources"][0]["content"]
        == "Summary\nInvented patient reports poor sleep."
    )
    assert f.client.calls == []
    # Exercise the real separate-tree OS protocol client against the real Core
    # facade/authority/database. Only workload identity acquisition is a fixture.
    import sys
    from dataclasses import asdict
    from importlib import import_module
    from pathlib import Path

    import httpx
    from pydantic import SecretStr

    os_source = (
        Path(__file__).resolve().parents[3] / "d3-compact-proof-worktree" / "src"
    )
    if not os_source.is_dir():
        pytest.skip(
            "The optional OS/Core wire check requires the local sibling integration checkout"
        )
    sys.path.insert(0, str(os_source))
    try:
        HttpCoreCaptureAdmission = import_module(
            "cosmas_os.adapters.core_capture_admission"
        ).HttpCoreCaptureAdmission
        ConnectorUnavailable = import_module(
            "cosmas_os.ports.bot_capabilities"
        ).ConnectorUnavailable
        core_capture = import_module("cosmas_os.ports.core_capture")
        CoreCaptureBindingSnapshot = core_capture.CoreCaptureBindingSnapshot
        CoreInteractionCredential = core_capture.CoreInteractionCredential

        def transport(request):
            assert request.url.host == "core.example.invalid"
            reply = connection.request(
                request.method,
                request.url.raw_path.decode(),
                headers=request.headers,
                content=request.content,
            )
            return httpx.Response(
                reply.status_code, headers=reply.headers, content=reply.content
            )

        port = HttpCoreCaptureAdmission(
            "https://core.example.invalid",
            lambda: "synthetic-workload",
            transport=httpx.MockTransport(transport),
        )
        try:
            binding = CoreCaptureBindingSnapshot(
                contract_version="1", **asdict(f.binding)
            )
            credential = CoreInteractionCredential(
                mount_context_token=SecretStr(f.caller.mount_context_token),
                interaction_ref=f.caller.interaction_ref,
                client_turn_id=f.caller.client_turn_id,
            )
            result = port.encounter_context(binding, credential)
            assert (
                result.snapshot_sha256 == response.json()["receipt"]["snapshot_sha256"]
            )
            f.case.identity.revoked_at = datetime.now(UTC)
            f.session.add(f.case.identity)
            f.session.commit()
            with pytest.raises(
                ConnectorUnavailable, match="core_capture_access_unavailable"
            ):
                port.encounter_context(binding, credential)
        finally:
            port.close()
    finally:
        sys.path.remove(str(os_source))


@pytest.mark.parametrize("fault", ["flag", "workload", "body", "duplicate", "large"])
def test_untrusted_read_rejected_before_source_access(monkeypatch, fault):
    connection = client()
    assert isinstance(connection.app, FastAPI)
    from app.configuration.scribe_capture import ScribeCaptureSettings

    connection.app.dependency_overrides[get_capture_settings] = (
        lambda: ScribeCaptureSettings(enabled=True, prep_enabled=fault != "flag")
    )
    if fault == "workload":

        def deny():
            raise HTTPException(401)

        connection.app.dependency_overrides[authenticate_scribe_capture_request] = deny
    monkeypatch.setattr(
        encounter_prep,
        "read_encounter_prep",
        lambda **_: pytest.fail("source access reached"),
    )
    body = '{"patient_id":"private sentinel"}'
    if fault == "duplicate":
        body = '{"workspace_id":"private sentinel","workspace_id":"private sentinel"}'
    if fault == "large":
        body = '"' + "private sentinel" * 300 + '"'
    result = connection.post(
        PATH, content=body, headers={**headers(), "Content-Type": "application/json"}
    )
    assert result.status_code == (
        503 if fault == "flag" else 401 if fault == "workload" else 422
    )
    assert "private sentinel" not in result.text
