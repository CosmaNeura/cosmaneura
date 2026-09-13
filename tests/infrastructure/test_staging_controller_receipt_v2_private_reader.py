from __future__ import annotations

import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from email.message import Message
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

import pytest
from pytest import MonkeyPatch

from scripts import read_staging_controller_receipt_v2 as ingress
from scripts import validate_staging_controller_receipt_v2 as content
from scripts import validate_staging_restore_launch_binding as launch
from scripts import validate_staging_restore_runtime_binding as runtime_binding

from tests.infrastructure import (
    test_staging_broker_launch_receipt_authentication as auth_helpers,
)
from tests.infrastructure import (
    test_staging_broker_launch_receipt_mailbox as mailbox_helpers,
)
from tests.infrastructure import test_staging_controller_receipt_v2 as content_helpers
from tests.infrastructure import test_staging_restore_launch_binding as launch_helpers
from tests.infrastructure import test_staging_restore_runtime_binding as runtime_helpers
from tests.infrastructure import test_staging_runtime_record_private_readers as wire

Failure = ingress.PrivateControllerReceiptV2Failure

NOW = datetime(2026, 9, 5, 12, 10, tzinfo=UTC)
EPOCH_NOW = 1_799_150_000
TOKEN = "controller-source-token"
LAST_MODIFIED = "Sat, 05 Sep 2026 12:06:00 GMT"
RETAINED_UNTIL = "Sat, 05 Dec 2026 12:06:00 GMT"


class Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        url: str | None = None,
        remove: str | None = None,
        replace: tuple[str, str] | None = None,
        duplicate: tuple[str, str] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.url = url
        self.headers = Message()
        values = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            "Content-MD5": base64.b64encode(
                hashlib.md5(body, usedforsecurity=False).digest()
            ).decode("ascii"),
            "ETag": '"strong-etag"',
            "Last-Modified": LAST_MODIFIED,
            "x-ms-immutability-policy-until-date": RETAINED_UNTIL,
            "x-ms-immutability-policy-mode": "Locked",
            "x-ms-legal-hold": "true",
            "x-ms-server-encrypted": "true",
            "x-ms-blob-type": "BlockBlob",
            "x-ms-version": "2021-12-02",
        }
        if remove is not None:
            values.pop(remove)
        if replace is not None:
            values[replace[0]] = replace[1]
        for name, value in values.items():
            self.headers.add_header(name, value)
        if duplicate is not None:
            self.headers.add_header(*duplicate)

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def geturl(self) -> str:
        assert self.url is not None
        return self.url

    def read(self, maximum: int) -> bytes:
        result, self.body = self.body[:maximum], self.body[maximum:]
        return result


class Opener:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[Any, int]] = []
        self.effects: list[Callable[[], None]] = []

    def open(self, request: Any, *, timeout: int) -> Any:
        self.calls.append((request, timeout))
        if self.effects:
            self.effects.pop(0)()
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        response.url = response.url or request.full_url
        return response


@contextmanager
def _no_deadline(_seconds: int) -> Iterator[None]:
    yield


def _configured_policies() -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = runtime_helpers._configured_policy()
    controller = content_helpers._configured_policy()
    provision_job_id = controller["fixed_bindings"]["provision_job_resource_id"]
    provision_identity_id = controller["record_chain_source"][
        "writer_identity_resource_id"
    ]
    replacements = {
        runtime["runtime"]["jobs"][action]["resource_id"]: (
            provision_job_id.replace("restore-provision", f"restore-{action}")
        )
        for action in ingress.ACTIONS
    } | {
        runtime["identities"]["jobs"][action]["resource_id"]: (
            provision_identity_id.replace("restore-provision", f"restore-{action}")
        )
        for action in ingress.ACTIONS
    }

    def replace_job_id(value: Any) -> Any:
        if type(value) is str:
            return replacements.get(value, value)
        if type(value) is list:
            return [replace_job_id(item) for item in value]
        if type(value) is dict:
            return {key: replace_job_id(item) for key, item in value.items()}
        return value

    def role_id(kind: str, prefix: int, suffix: int) -> str:
        return content_helpers._rid(
            content_helpers.CONTROL_GROUP,
            "Microsoft.Authorization",
            kind,
            f"{prefix}0000000-0000-4000-8000-00000000000{suffix}",
        )

    runtime = replace_job_id(runtime)
    marker = runtime["private_control_marker_store"]
    account = (
        f"/subscriptions/{content_helpers.SUBSCRIPTION}/resourceGroups/runtime/"
        "providers/Microsoft.Storage/storageAccounts/restoremarkers"
    )
    marker["account_resource_id"] = account
    marker["container_resource_ids"] = {
        kind: f"{account}/blobServices/default/containers/{name}"
        for kind, name in marker["container_names"].items()
    }
    source = controller["record_chain_source"]
    jobs = runtime["identities"]["jobs"]
    source.update(
        {
            "account_resource_id": account,
            "account_host": marker["account_host"],
            "private_endpoint_resource_id": marker["private_endpoint_resource_id"],
            "writer_identity_resource_id": jobs["provision"]["resource_id"],
            "writer_role_assignment_id": role_id("roleAssignments", 1, 1),
            "create_role_definition_id": role_id("roleDefinitions", 1, 2),
            "create_role_permissions_sha256": f"sha256:{'1' * 64}",
            "read_role_definition_id": role_id("roleDefinitions", 1, 3),
            "read_role_permissions_sha256": f"sha256:{'2' * 64}",
            "immutability_configuration_evidence_sha256": f"sha256:{'3' * 64}",
        }
    )
    source["container_resource_ids"] = {
        kind: f"{account}/blobServices/default/containers/{name}"
        for kind, name in source["container_names"].items()
    }
    source["controller_immutability_policy_resource_id"] = (
        f"{source['container_resource_ids']['controller_receipt']}"
        "/immutabilityPolicies/default"
    )
    source["reader_identity_resource_ids"] = {
        action: runtime["identities"]["jobs"][action]["resource_id"]
        for action in ingress.ACTIONS
    }
    source["reader_identity_client_ids"] = {
        action: runtime["identities"]["jobs"][action]["client_id"]
        for action in ingress.ACTIONS
    }
    source["reader_role_assignment_ids"] = {
        action: role_id("roleAssignments", 2, index)
        for index, action in enumerate(("provision", "cleanup"), 1)
    }
    controller["fixed_bindings"].update(
        {
            "provision_job_resource_id": provision_job_id,
            "provision_job_uami_client_id": jobs["provision"]["client_id"],
            "provision_job_uami_principal_id": jobs["provision"]["principal_id"],
        }
    )
    for action in ingress.ACTIONS:
        projection, _pointer, _template = launch._job_projection(
            runtime_helpers._job_document(runtime, action),
            policy=runtime,
            job_kind=action,
        )
        runtime["runtime"]["jobs"][action]["configuration_template_sha256"] = (
            runtime_binding._digest(runtime_binding._canonical(projection))
        )
    assert not ingress._placeholder_values(controller)
    return runtime, controller


def _launch_authority(
    monkeypatch: MonkeyPatch,
    runtime: dict[str, Any],
    *,
    action: str,
    rehearsal_id: str,
    bounded_sweep: bool = False,
    source_mismatch: bool = False,
    signed_observed_at: str = "2026-09-05T12:03:00Z",
    authenticated_observed_at: datetime = datetime(2026, 9, 5, 12, 4, tzinfo=UTC),
) -> tuple[launch.AuthenticatedBrokerLaunchReceipt, launch.ValidatedLaunchRecord]:
    broker = runtime_binding._parse_policy(mailbox_helpers._receipt(runtime, action))
    raw, _digest_value, _uri = launch_helpers._record(
        runtime,
        action=action,
        runtime_record_sha256=broker["runtime_record_sha256"],
    )
    record_document = runtime_binding._parse_policy(raw)
    evidence = content_helpers._evidence()
    for name in ("workflow_sha", "run_id", "run_attempt"):
        record_document[name] = evidence[name]
    if source_mismatch:
        record_document["run_id"] = "54321"
    record_document["created_at"] = "2026-09-05T12:01:00Z"
    record_document["expires_at"] = "2026-09-05T13:01:00Z"
    if action == "provision":
        record_document["payload"]["rehearsal_id"] = rehearsal_id
        for name, item in record_document["payload"]["evidence_files"].items():
            item["versioned_https_uri"] = (
                "https://restoreevidence.blob.core.windows.net/"
                f"restore-evidence-v1/{rehearsal_id}/{name}?versionid="
                f"{item['version_id']}"
            )
        item = record_document["payload"]["github_attestation_bundle"]
        item["versioned_https_uri"] = (
            "https://restoreevidence.blob.core.windows.net/"
            f"restore-evidence-v1/{rehearsal_id}/attestation.json?versionid="
            f"{item['version_id']}"
        )
    elif bounded_sweep:
        record_document["payload"] = {"mode": "bounded_sweep"}
    else:
        record_document["payload"] = {
            "mode": "selected_rehearsal",
            "rehearsal_id": rehearsal_id,
        }
    record_bytes = runtime_binding._canonical(record_document)
    record_sha256 = runtime_binding._digest(record_bytes)
    mailbox = runtime["launch_binding"]["mailbox"]
    record_uri = (
        f"https://{mailbox['account_host']}/{mailbox['container_names'][action]}/"
        f"{record_sha256[7:]}.json"
    )
    record = launch.validate_launch_record(
        runtime,
        record_bytes,
        record_sha256=record_sha256,
        record_uri=record_uri,
    )
    broker.update(
        {
            "launch_record_uri": record_uri,
            "launch_record_sha256": record_sha256,
            "start_client_request_id": launch._client_request_id(record_sha256),
            "execution_resource_id": (
                f"{runtime['runtime']['jobs'][action]['resource_id']}/executions/"
                f"{mailbox_helpers.EXECUTION_NAME}"
            ),
            "provision_job_projection_sha256": runtime["runtime"]["jobs"]["provision"][
                "configuration_template_sha256"
            ],
            "observed_at": signed_observed_at,
        }
    )
    receipt_bytes = runtime_binding._canonical(broker)
    transport = mailbox_helpers.FakeTransport(
        {
            "receipt": receipt_bytes,
            "signature_envelope": auth_helpers._envelope(runtime, receipt_bytes),
        }
    )
    receipt = mailbox_helpers._receive(
        monkeypatch,
        runtime,
        transport,
        mailbox_helpers.Clock(),
        action=action,
        wall_clock=lambda: authenticated_observed_at,
    )
    return receipt, record


def _records(
    runtime: dict[str, Any],
    controller: dict[str, Any],
    authenticated_receipt: launch.AuthenticatedBrokerLaunchReceipt,
    launch_record: launch.ValidatedLaunchRecord,
) -> dict[str, bytes]:
    _old_policy, intent, submission, receipt = content_helpers._fixture()
    jobs = runtime["identities"]["jobs"]
    intent["provision_client_id"] = jobs["provision"]["client_id"]
    intent["provision_principal_id"] = jobs["provision"]["principal_id"]
    intent_sha256 = content._digest(content._canonical(intent))
    submission["intent_sha256"] = intent_sha256
    submission["restore_request_sha256"] = content._restore_request_sha256(
        intent, intent_sha256
    )
    receipt["policy_binding_sha256"] = content._policy_binding_digest(controller)
    receipt["identity"].update(
        {
            "provision_job_resource_id": runtime["runtime"]["jobs"]["provision"][
                "resource_id"
            ],
            "provision_job_uami_resource_id": jobs["provision"]["resource_id"],
            "provision_job_uami_client_id": jobs["provision"]["client_id"],
            "provision_job_uami_principal_id": jobs["provision"]["principal_id"],
        }
    )
    receipt["authorization"].update(
        {
            "intent_sha256": intent_sha256,
            "submission_sha256": content._digest(content._canonical(submission)),
            "restore_request_sha256": submission["restore_request_sha256"],
        }
    )
    if authenticated_receipt.action == "provision":
        broker = runtime_binding._parse_policy(authenticated_receipt.receipt_bytes)
        receipt["launch"] = {
            "launch_record_sha256": launch_record.record_sha256,
            "broker_launch_receipt_sha256": authenticated_receipt.receipt_sha256,
            "runtime_record_sha256": broker["runtime_record_sha256"],
            "runtime_signature_bundle_sha256": broker[
                "runtime_signature_bundle_sha256"
            ],
            "execution_resource_id": broker["execution_resource_id"],
            "execution_name": authenticated_receipt.execution_name,
            "launch_expires_at": launch_record.expires_at.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "provision_job_configuration_template_sha256": broker[
                "provision_job_projection_sha256"
            ],
            "provision_job_image_reference": broker["provision_job_image_reference"],
        }
    return {
        "intent": content._canonical(intent),
        "submission": content._canonical(submission),
        "controller_receipt": content._canonical(receipt),
    }


def _token(runtime: dict[str, Any], action: str) -> bytes:
    return json.dumps(
        {
            "access_token": TOKEN,
            "client_id": runtime["identities"]["jobs"][action]["client_id"],
            "expires_on": str(EPOCH_NOW + 3600),
            "resource": "https://storage.azure.com/",
            "token_type": "Bearer",
        },
        separators=(",", ":"),
    ).encode()


def _case(
    monkeypatch: MonkeyPatch,
    *,
    action: str = "provision",
    now: datetime = NOW,
    bounded_sweep: bool = False,
    source_mismatch: bool = False,
    signed_observed_at: str = "2026-09-05T12:03:00Z",
    authenticated_observed_at: datetime = datetime(2026, 9, 5, 12, 4, tzinfo=UTC),
    response_factory: Callable[[str, bytes], Response] = lambda _kind, raw: Response(
        raw
    ),
    monotonic: Callable[[], float] = lambda: 10.0,
) -> dict[str, Any]:
    runtime, controller = _configured_policies()
    _unused, intent, _submission, _receipt = content_helpers._fixture()
    rehearsal_id = intent["rehearsal_id"]
    authenticated_receipt, record = _launch_authority(
        monkeypatch,
        runtime,
        action=action,
        rehearsal_id=rehearsal_id,
        bounded_sweep=bounded_sweep,
        source_mismatch=source_mismatch,
        signed_observed_at=signed_observed_at,
        authenticated_observed_at=authenticated_observed_at,
    )
    records = (
        _records(runtime, controller, authenticated_receipt, record)
        if not bounded_sweep
        else {}
    )
    identity = wire.Opener(
        [wire.Response(_token(runtime, action))], name="identity", events=[]
    )
    blob = Opener(
        [response_factory(kind, records[kind]) for kind in ingress.RECORD_KINDS]
        if records
        else []
    )
    reader = ingress.ManagedIdentityPrivateControllerReceiptV2Reader(
        runtime_policy=runtime,
        controller_policy=controller,
        clock=lambda: now,
        identity_opener=identity,
        blob_opener=blob,
        epoch_clock=lambda: EPOCH_NOW,
        monotonic=monotonic,
        deadline_factory=_no_deadline,
    )
    monkeypatch.setenv("IDENTITY_ENDPOINT", wire.IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", wire.IDENTITY_HEADER)
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, mailbox_helpers.EXECUTION_NAME)
    return {
        "runtime": runtime,
        "controller": controller,
        "receipt": authenticated_receipt,
        "record": record,
        "records": records,
        "identity": identity,
        "blob": blob,
        "reader": reader,
    }


def _read(
    case: dict[str, Any],
) -> ingress.AuthenticatedControllerReceiptV2RecordChainSource:
    return case["reader"].read_and_authenticate(
        authenticated_receipt=case["receipt"],
        launch_record=case["record"],
    )


def test_provision_reads_exact_chain_once_and_returns_narrow_source_authentication(
    monkeypatch: MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    source = case["controller"]["record_chain_source"]
    account_host = source["account_host"]
    expected_uris = [
        f"https://{source['account_host']}/{source['container_names'][kind]}/"
        f"{case['record'].rehearsal_id}.json"
        for kind in ingress.RECORD_KINDS
    ]
    source["account_host"] = "caller-mutation.invalid"
    result = _read(case)
    source["account_host"] = account_host

    assert result.record_source_authenticated is True
    assert result.provider_facts_authenticated is False
    assert result.runtime_authority is False
    assert result.writer_identity_or_private_route_proven is False
    assert result._is_authenticated_for(
        runtime_policy=case["runtime"],
        controller_policy=case["controller"],
        authenticated_receipt=case["receipt"],
        launch_record=case["record"],
    )
    assert len(case["identity"].calls) == 1
    assert [call[0].full_url for call in case["blob"].calls] == expected_uris
    for request, timeout in case["blob"].calls:
        headers = {name.lower(): value for name, value in request.header_items()}
        assert request.method == "GET"
        assert urlsplit(request.full_url).query == ""
        assert headers == {
            "accept": "application/json",
            "accept-encoding": "identity",
            "authorization": f"Bearer {TOKEN}",
            "x-ms-version": "2021-12-02",
        }
        assert 1 <= timeout <= 15


def test_selected_cleanup_uses_cleanup_identity_and_accepts_expired_content(
    monkeypatch: MonkeyPatch,
) -> None:
    case = _case(
        monkeypatch,
        action="cleanup",
        now=datetime(2026, 9, 5, 13, 0, tzinfo=UTC),
    )
    result = _read(case)
    token_request = case["identity"].calls[0][0]
    assert result.action == "cleanup"
    assert (
        runtime_binding._parse_policy(result.validated_content.receipt_bytes)[
            "expires_at"
        ]
        < "2026-09-05T13:00:00Z"
    )
    assert case["runtime"]["identities"]["jobs"]["cleanup"]["client_id"] in (
        token_request.full_url
    )
    other_receipt, other_record = _launch_authority(
        monkeypatch,
        case["runtime"],
        action="cleanup",
        rehearsal_id=result.rehearsal_id,
        source_mismatch=True,
    )
    assert not result._is_authenticated_for(
        runtime_policy=case["runtime"],
        controller_policy=case["controller"],
        authenticated_receipt=other_receipt,
        launch_record=other_record,
    )


def test_bounded_sweep_refuses_before_identity_or_blob_io(
    monkeypatch: MonkeyPatch,
) -> None:
    case = _case(monkeypatch, action="cleanup", bounded_sweep=True)
    with pytest.raises(Failure, match="authority_invalid"):
        _read(case)
    assert case["identity"].calls == []
    assert case["blob"].calls == []


@pytest.mark.parametrize(
    ("signed", "authenticated"),
    [
        ("2026-09-05T12:00:00Z", datetime(2026, 9, 5, 12, 4, tzinfo=UTC)),
        ("2026-09-05T13:02:00Z", datetime(2026, 9, 5, 12, 4, tzinfo=UTC)),
        ("2026-09-05T12:03:00Z", datetime(2026, 9, 5, 13, 2, tzinfo=UTC)),
    ],
)
def test_launch_observation_after_expiry_refuses_before_io(
    monkeypatch: MonkeyPatch, signed: str, authenticated: datetime
) -> None:
    case = _case(
        monkeypatch,
        signed_observed_at=signed,
        authenticated_observed_at=authenticated,
    )
    with pytest.raises(Failure):
        _read(case)
    assert case["identity"].calls == case["blob"].calls == []


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("Content-Type", "text/plain"),
        ("Content-Length", "0001"),
        ("Content-MD5", "AAAA"),
        ("ETag", 'W/"weak"'),
        ("Last-Modified", "not-a-date"),
        ("x-ms-immutability-policy-until-date", LAST_MODIFIED),
        ("x-ms-immutability-policy-mode", "Unlocked"),
        ("x-ms-legal-hold", "false"),
        ("x-ms-server-encrypted", "false"),
        ("x-ms-blob-type", "AppendBlob"),
        ("x-ms-version", "2020-01-01"),
    ],
)
def test_worm_metadata_and_framing_fail_closed(
    monkeypatch: MonkeyPatch, header: str, value: str
) -> None:
    case = _case(
        monkeypatch,
        response_factory=lambda kind, raw: Response(
            raw, replace=(header, value) if kind == "intent" else None
        ),
    )
    with pytest.raises(Failure, match="record_invalid"):
        _read(case)
    assert len(case["blob"].calls) == 1


@pytest.mark.parametrize(
    "response_factory",
    [
        lambda kind, raw: Response(
            raw, remove="Content-Length" if kind == "intent" else None
        ),
        lambda kind, raw: Response(
            raw,
            duplicate=("Content-Length", str(len(raw))) if kind == "intent" else None,
        ),
        lambda kind, raw: Response(
            raw,
            duplicate=("ETag", '"other"') if kind == "intent" else None,
        ),
        lambda kind, raw: Response(raw, status=404 if kind == "intent" else 200),
        lambda kind, raw: Response(
            raw,
            url="https://attacker.invalid/object" if kind == "intent" else None,
        ),
    ],
)
def test_missing_duplicate_non_success_and_redirect_responses_refuse_once(
    monkeypatch: MonkeyPatch,
    response_factory: Callable[[str, bytes], Response],
) -> None:
    case = _case(monkeypatch, response_factory=response_factory)
    with pytest.raises(Failure) as error:
        _read(case)
    assert str(error.value) == "controller_receipt_ingress.record_invalid"
    assert len(case["blob"].calls) == 1


def test_content_and_provision_launch_mismatch_are_bounded(
    monkeypatch: MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    secret = "f" * 64
    receipt = runtime_binding._parse_policy(case["records"]["controller_receipt"])
    receipt["launch"]["broker_launch_receipt_sha256"] = f"sha256:{secret}"
    case["blob"].responses[-1] = Response(content._canonical(receipt))
    with pytest.raises(Failure) as error:
        _read(case)
    assert str(error.value) == "controller_receipt_ingress.authority_invalid"
    assert secret not in str(error.value)
    source_mismatch = _case(monkeypatch, source_mismatch=True)
    with pytest.raises(Failure, match="authority_invalid"):
        _read(source_mismatch)


def test_reader_burns_on_failure_and_allows_only_one_concurrent_call(
    monkeypatch: MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    case["blob"].responses[0] = Response(b"{}", status=404)
    with pytest.raises(Failure):
        _read(case)
    with pytest.raises(Failure, match="already_used"):
        _read(case)

    concurrent = _case(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: capture(concurrent), range(2)))
    assert sorted(results) == ["already_used", "ok"]


def capture(case: dict[str, Any]) -> str:
    try:
        _read(case)
        return "ok"
    except Failure as error:
        return str(error).removeprefix("controller_receipt_ingress.")


@pytest.mark.parametrize("stage", ["identity", "first_blob", "last_blob"])
def test_authority_and_execution_are_rechecked_after_every_stage(
    monkeypatch: MonkeyPatch, stage: str
) -> None:
    case = _case(monkeypatch)

    def mutate() -> None:
        object.__setattr__(case["receipt"], "execution_name", "changed-execution")

    if stage == "identity":
        case["identity"].effects = [mutate]
    elif stage == "first_blob":
        case["blob"].effects = [mutate]
    else:
        case["blob"].effects = [lambda: None, lambda: None, mutate]
    with pytest.raises(Failure, match="authority_invalid"):
        _read(case)


@pytest.mark.parametrize(
    "mutation",
    [
        "controller_placeholder",
        "runtime_policy_pin",
        "account_host",
        "immutability_policy",
        "reader_identity",
        "reader_client",
        "container",
        "private_endpoint",
    ],
)
def test_cross_policy_disagreement_refuses_before_io(
    monkeypatch: MonkeyPatch, mutation: str
) -> None:
    case = _case(monkeypatch)
    source = case["controller"]["record_chain_source"]
    if mutation == "controller_placeholder":
        source["immutability_configuration_evidence_sha256"] = (
            "CONFIGURE_EXACT_WRONG_SHA256"
        )
    elif mutation == "runtime_policy_pin":
        case["runtime"]["authenticated_controller_receipt"][
            "content_contract_policy_sha256"
        ] = f"sha256:{'9' * 64}"
    elif mutation == "account_host":
        source["account_host"] = "different.blob.core.windows.net"
        case["runtime"]["private_control_marker_store"]["account_host"] = source[
            "account_host"
        ]
    elif mutation == "immutability_policy":
        source["controller_immutability_policy_resource_id"] += "-other"
    elif mutation == "reader_identity":
        source["reader_identity_resource_ids"]["provision"] += "-other"
    elif mutation == "reader_client":
        source["reader_identity_client_ids"]["provision"] = (
            "90000000-0000-4000-8000-000000000009"
        )
    elif mutation == "container":
        source["container_names"]["intent"] = "other-intents"
    else:
        source["private_endpoint_resource_id"] += "-other"
    with pytest.raises(Failure, match="policy_invalid"):
        ingress.ManagedIdentityPrivateControllerReceiptV2Reader(
            runtime_policy=case["runtime"],
            controller_policy=case["controller"],
            clock=lambda: NOW,
        )
    assert case["identity"].calls == []
    assert case["blob"].calls == []


def test_sealed_results_detect_field_body_and_metadata_tampering(
    monkeypatch: MonkeyPatch,
) -> None:
    with pytest.raises(Failure, match="result_unsealed"):
        ingress.AuthenticatedControllerReceiptV2RecordChainSource()
    case = _case(monkeypatch)
    result = _read(case)
    assert TOKEN not in repr(result)
    object.__setattr__(result.validated_content, "target_fqdn", "evil.example.com")
    assert not result._is_authenticated_for(
        runtime_policy=case["runtime"],
        controller_policy=case["controller"],
        authenticated_receipt=case["receipt"],
        launch_record=case["record"],
    )
    object.__setattr__(result.validated_content, "target_fqdn", "ignored")
    object.__setattr__(result._objects[0], "body", b"{}")
    assert not result._objects[0]._is_authenticated()
    object.__setattr__(result._objects[0], "body", case["records"]["intent"])
    object.__setattr__(result._objects[0], "worm_evidence", ("locked",) * 5)
    assert not result._objects[0]._is_authenticated()


def test_deadline_and_trusted_clock_regression_fail_without_retry(
    monkeypatch: MonkeyPatch,
) -> None:
    values = iter([10.0, 10.0, 70.0])
    case = _case(monkeypatch, monotonic=lambda: next(values))
    with pytest.raises(Failure, match="deadline"):
        _read(case)
    assert case["blob"].calls == []

    utc_values = iter([NOW, NOW, NOW.replace(minute=9)])
    regressed = _case(monkeypatch)
    regressed["reader"] = ingress.ManagedIdentityPrivateControllerReceiptV2Reader(
        runtime_policy=regressed["runtime"],
        controller_policy=regressed["controller"],
        clock=lambda: next(utc_values),
        identity_opener=regressed["identity"],
        blob_opener=regressed["blob"],
        epoch_clock=lambda: EPOCH_NOW,
        monotonic=lambda: 10.0,
        deadline_factory=_no_deadline,
    )
    with pytest.raises(Failure, match="trusted_clock_regressed"):
        _read(regressed)
    assert regressed["blob"].calls == []


def test_policy_records_only_dormant_reader_implementation() -> None:
    policy = content.load_controller_receipt_v2_policy()
    status = policy["status"]
    source = policy["record_chain_source"]
    retrieval = source["retrieval"]
    assert status["private_worm_reader_implemented"] is True
    assert status["content_validator_implemented"] is True
    assert status["activation_blockers"] == [
        "controller_receipt_v2_producer_unimplemented",
        "controller_observation_authorities_unimplemented",
        "authenticated_controller_receipt_runtime_binding_unintegrated",
        "workflow_integration_unimplemented",
    ]
    assert all(
        status[name] is False
        for name in (
            "controller_observation_authorities_implemented",
            "controller_receipt_producer_implemented",
            "executable",
            "runtime_consumer_integrated",
            "workflow_integrated",
        )
    )
    assert source["source_authentication_result"] == {
        "kind": "private-worm-authenticated-controller-receipt-v2-content",
        "provider_facts_authenticated": False,
        "record_source_authenticated": True,
        "runtime_authority": False,
        "writer_identity_or_private_route_proven": False,
    }
    assert retrieval["maximum_storage_token_requests"] == 1
    assert retrieval["maximum_record_get_requests"] == 3
    assert retrieval["maximum_total_http_exchanges"] == 4
    assert retrieval["listing_permitted"] is False
    assert retrieval["retry_permitted"] is False
    assert retrieval["polling_permitted"] is False
    assert source["caller_path_uri_or_mapping_permitted"] is False


def test_public_reader_api_has_no_selector_and_factory_refuses_placeholders() -> None:
    import inspect

    parameters = inspect.signature(
        ingress.ManagedIdentityPrivateControllerReceiptV2Reader.read_and_authenticate
    ).parameters
    assert set(parameters) == {
        "self",
        "authenticated_receipt",
        "launch_record",
    }
    runtime = runtime_helpers._configured_policy()
    controller = content.load_controller_receipt_v2_policy()
    with pytest.raises(Failure, match="policy_invalid"):
        ingress.build_private_controller_receipt_v2_reader(
            runtime_policy=runtime,
            controller_policy=controller,
            clock=lambda: NOW,
        )
