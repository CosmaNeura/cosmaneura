from __future__ import annotations

import base64
import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import timedelta
from typing import Any, Callable

import pytest

from scripts import read_staging_restore_evidence_material as ingress
from scripts import validate_staging_restore_launch_binding as launch
from scripts import validate_staging_restore_runtime_binding as binding

from tests.infrastructure import (
    test_staging_broker_launch_receipt_authentication as auth_helpers,
)
from tests.infrastructure import (
    test_staging_broker_launch_receipt_mailbox as mailbox_helpers,
)
from tests.infrastructure import test_staging_restore_launch_binding as launch_helpers
from tests.infrastructure import test_staging_restore_runtime_binding as policy_helpers
from tests.infrastructure import test_staging_runtime_record_private_readers as wire

EVIDENCE_BYTES = {
    "SHA256SUMS": b"exact checksums\n",
    "candidate.json": b'{"candidate":"exact"}',
    "evidence-manifest.json": b'{"manifest":"exact"}',
    "inventory.json": b'{"inventory":"exact"}',
    "quiescence.json": b'{"quiescence":"exact"}',
    "server-scope.json": b'{"server_scope":"exact"}',
}
ATTESTATION_BYTES = b'{"sigstore":"exact-evidence-attestation"}'
HMAC_KEY_BYTES = bytes(range(32))
STORAGE_TOKEN = "private-storage-token"
VAULT_TOKEN = "private-vault-token"
STAGE_EXCHANGES = (
    ("storage_token", 1),
    ("first_evidence", 2),
    ("last_evidence", 7),
    ("attestation", 8),
    ("vault_token", 9),
    ("hmac_secret", 10),
)


def _launch_record(policy: dict[str, Any]) -> launch.ValidatedLaunchRecord:
    runtime_sha256 = mailbox_helpers._receipt(policy, "provision")
    receipt_document = binding._parse_policy(runtime_sha256)
    raw, _old_digest, _old_uri = launch_helpers._record(
        policy,
        action="provision",
        runtime_record_sha256=receipt_document["runtime_record_sha256"],
    )
    document = binding._parse_policy(raw)
    evidence = document["payload"]["evidence_files"]
    for name, body in EVIDENCE_BYTES.items():
        evidence[name]["sha256"] = binding._digest(body)
    document["payload"]["github_attestation_bundle"]["sha256"] = binding._digest(
        ATTESTATION_BYTES
    )
    document["payload"]["server_scope_hmac_key"]["decoded_sha256"] = binding._digest(
        HMAC_KEY_BYTES
    )
    record_bytes = binding._canonical(document)
    record_sha256 = binding._digest(record_bytes)
    mailbox = policy["launch_binding"]["mailbox"]
    record_uri = (
        f"https://{mailbox['account_host']}/"
        f"{mailbox['container_names']['provision']}/{record_sha256[7:]}.json"
    )
    return launch.validate_launch_record(
        policy,
        record_bytes,
        record_sha256=record_sha256,
        record_uri=record_uri,
    )


def _authenticated_receipt(
    monkeypatch: pytest.MonkeyPatch,
    policy: dict[str, Any],
    record: launch.ValidatedLaunchRecord,
) -> launch.AuthenticatedBrokerLaunchReceipt:
    document = binding._parse_policy(mailbox_helpers._receipt(policy, "provision"))
    document["launch_record_uri"] = record.record_uri
    document["launch_record_sha256"] = record.record_sha256
    document["start_client_request_id"] = launch._client_request_id(
        record.record_sha256
    )
    receipt_bytes = binding._canonical(document)
    transport = mailbox_helpers.FakeTransport(
        {
            "receipt": receipt_bytes,
            "signature_envelope": auth_helpers._envelope(policy, receipt_bytes),
        }
    )
    return mailbox_helpers._receive(
        monkeypatch,
        policy,
        transport,
        mailbox_helpers.Clock(),
    )


def _token(policy: dict[str, Any], resource: str, value: str) -> bytes:
    return wire._token(policy, resource, value)


def _vault_body(policy: dict[str, Any], encoded: str | None = None) -> bytes:
    secret = policy["evidence_ingress"]["private_versioned_secret_source"]["secrets"][
        "server_scope_hmac_key"
    ]
    value = (
        base64.urlsafe_b64encode(HMAC_KEY_BYTES).rstrip(b"=").decode("ascii")
        if encoded is None
        else encoded
    )
    return json.dumps(
        {
            "value": value,
            "id": secret["versioned_https_uri"],
            "attributes": {"enabled": True},
        },
        separators=(",", ":"),
    ).encode()


def _blob_responses(
    *,
    evidence: dict[str, bytes] | None = None,
    attestation: bytes = ATTESTATION_BYTES,
) -> list[wire.Response]:
    selected = EVIDENCE_BYTES if evidence is None else evidence
    responses = [
        wire.Response(
            selected[name],
            content_type="text/plain" if name == "SHA256SUMS" else "application/json",
        )
        for name in EVIDENCE_BYTES
    ]
    responses.append(
        wire.Response(
            attestation,
            content_type="application/vnd.dev.sigstore.bundle.v0.3+json",
        )
    )
    return responses


def _case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    blob_responses: list[wire.Response] | None = None,
    vault_body: bytes | None = None,
    clock: Callable[[], Any] = lambda: launch_helpers.VALIDATION_NOW,
    monotonic: Callable[[], float] = lambda: 10.0,
) -> dict[str, Any]:
    policy = policy_helpers._configured_policy()
    record = _launch_record(policy)
    receipt = _authenticated_receipt(monkeypatch, policy, record)
    blob = policy["evidence_ingress"]["private_restore_evidence_container"]
    vault_resource = policy["evidence_ingress"]["private_versioned_secret_source"][
        "oauth_resource"
    ]
    events: list[str] = []
    identity = wire.Opener(
        [
            wire.Response(_token(policy, blob["oauth_resource"], STORAGE_TOKEN)),
            wire.Response(_token(policy, vault_resource, VAULT_TOKEN)),
        ],
        name="identity",
        events=events,
    )
    blob_opener = wire.Opener(
        blob_responses or _blob_responses(), name="blob", events=events
    )
    vault = wire.Opener(
        [wire.Response(vault_body or _vault_body(policy))],
        name="vault",
        events=events,
    )
    reader = ingress.ManagedIdentityPrivateEvidenceMaterialReader(
        clock=clock,
        identity_opener=identity,
        blob_opener=blob_opener,
        vault_opener=vault,
        epoch_clock=lambda: wire.EPOCH_NOW,
        monotonic=monotonic,
        deadline_factory=wire._no_deadline,
    )
    monkeypatch.setenv("IDENTITY_ENDPOINT", wire.IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", wire.IDENTITY_HEADER)
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, mailbox_helpers.EXECUTION_NAME)
    return {
        "policy": policy,
        "record": record,
        "receipt": receipt,
        "reader": reader,
        "identity": identity,
        "blob": blob_opener,
        "vault": vault,
        "events": events,
    }


def _read(case: dict[str, Any]) -> ingress.PrivateProvisionInputs:
    return case["reader"].read_provision_inputs(
        case["policy"], case["receipt"], case["record"]
    )


def _headers(request: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


def _after_stage(case: dict[str, Any], stage: str, effect: Callable[[], None]) -> None:
    def no_effect() -> None:
        return None

    if stage == "storage_token":
        case["identity"].effects = [effect]
    elif stage == "first_evidence":
        case["blob"].effects = [effect]
    elif stage == "last_evidence":
        case["blob"].effects = [no_effect] * 5 + [effect]
    elif stage == "attestation":
        case["blob"].effects = [no_effect] * 6 + [effect]
    elif stage == "vault_token":
        case["identity"].effects = [no_effect, effect]
    else:
        case["vault"].effects = [effect]


def test_reads_exact_ordered_material_in_ten_http_exchanges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    result = _read(case)
    policy = case["policy"]
    record = case["record"]
    receipt = case["receipt"]

    assert result._is_sealed()
    assert result._is_bound_to(policy, receipt, record)
    assert result.evidence_entries == tuple(EVIDENCE_BYTES.items())
    assert result.evidence_files() == EVIDENCE_BYTES
    detached = result.evidence_files()
    detached.clear()
    assert result.evidence_files() == EVIDENCE_BYTES
    assert result.attestation_bundle_bytes == ATTESTATION_BYTES
    assert result.server_scope_hmac_key_bytes == HMAC_KEY_BYTES
    assert result.receipt_sha256 == receipt.receipt_sha256
    assert result.launch_record_sha256 == record.record_sha256
    assert result.execution_binding == (
        f"{policy['runtime']['jobs']['provision']['resource_id']}/executions/"
        f"{mailbox_helpers.EXECUTION_NAME}"
    )
    assert case["events"] == [
        "identity",
        *("blob" for _ in range(7)),
        "identity",
        "vault",
    ]
    assert sum(len(case[name].calls) for name in ("identity", "blob", "vault")) == 10

    evidence_authorities = record.evidence_file_authorities
    assert [call[0].full_url for call in case["blob"].calls] == [
        *(item.versioned_https_uri for item in evidence_authorities),
        record.attestation_bundle_authority.versioned_https_uri,
    ]
    expected_media = policy["evidence_ingress"]["private_restore_evidence_container"][
        "retrieval"
    ]["evidence_file_media_types"]
    assert [_headers(call[0])["accept"] for call in case["blob"].calls[:6]] == list(
        expected_media.values()
    )
    assert all(call[1] == 15 for call in case["blob"].calls)
    assert all(
        _headers(call[0])["authorization"] == f"Bearer {STORAGE_TOKEN}"
        for call in case["blob"].calls
    )

    storage_request = case["identity"].calls[0][0]
    vault_request = case["identity"].calls[1][0]
    storage_query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(storage_request.full_url).query
    )
    vault_query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(vault_request.full_url).query
    )
    client_id = policy["identities"]["jobs"]["provision"]["client_id"]
    assert storage_query == {
        "api-version": ["2019-08-01"],
        "client_id": [client_id],
        "resource": ["https://storage.azure.com/"],
    }
    assert vault_query == {
        "api-version": ["2019-08-01"],
        "client_id": [client_id],
        "resource": ["https://vault.azure.net"],
    }
    secret = policy["evidence_ingress"]["private_versioned_secret_source"]["secrets"][
        "server_scope_hmac_key"
    ]
    secret_request = case["vault"].calls[0][0]
    assert secret_request.full_url == (
        f"{secret['versioned_https_uri']}?api-version=7.4"
    )
    assert _headers(secret_request)["authorization"] == f"Bearer {VAULT_TOKEN}"


def test_result_is_init_disabled_tamper_evident_and_nonsecret_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.result_unsealed$",
    ):
        ingress.PrivateProvisionInputs()
    result = _read(case)
    rendered = repr(result)
    assert "exact checksums" not in rendered
    assert STORAGE_TOKEN not in rendered
    assert VAULT_TOKEN not in rendered
    assert repr(HMAC_KEY_BYTES) not in rendered
    assert tuple(field.name for field in fields(result)) == (
        "evidence_entries",
        "attestation_bundle_bytes",
        "server_scope_hmac_key_bytes",
        "receipt_sha256",
        "launch_record_sha256",
        "execution_binding",
        "_material_sha256",
        "_seal",
    )
    object.__setattr__(
        result,
        "evidence_entries",
        (("SHA256SUMS", b"substituted"), *result.evidence_entries[1:]),
    )
    assert result._is_sealed() is False
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.result_invalid$",
    ):
        result.evidence_files()


def test_reader_is_burned_before_validation_and_concurrency_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda _index: _attempt(case),
                range(2),
            )
        )
    assert sorted(outcomes) == ["evidence_ingress.already_used", "success"]
    assert len(case["identity"].calls) == 2
    assert len(case["blob"].calls) == 7
    assert len(case["vault"].calls) == 1

    invalid = _case(monkeypatch)
    invalid["policy"]["evidence_ingress"]["private_restore_evidence_container"][
        "reader_status"
    ] = "changed"
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.policy_invalid$",
    ):
        _read(invalid)
    invalid["policy"] = policy_helpers._configured_policy()
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.already_used$",
    ):
        _read(invalid)


def _attempt(case: dict[str, Any]) -> str:
    try:
        _read(case)
    except ingress.PrivateEvidenceMaterialFailure as error:
        return str(error)
    return "success"


@pytest.mark.parametrize("target", ("receipt", "record", "execution"))
@pytest.mark.parametrize(("stage", "expected_exchanges"), STAGE_EXCHANGES)
def test_authority_and_execution_are_rechecked_after_each_stage(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    stage: str,
    expected_exchanges: int,
) -> None:
    case = _case(monkeypatch)

    def mutate() -> None:
        if target == "receipt":
            object.__setattr__(case["receipt"], "receipt_sha256", f"sha256:{'0' * 64}")
        elif target == "record":
            object.__setattr__(case["record"], "record_sha256", f"sha256:{'0' * 64}")
        else:
            monkeypatch.setenv(launch.EXECUTION_NAME_ENV, "execution-2")

    _after_stage(case, stage, mutate)
    expected = (
        "evidence_ingress.execution_identity_invalid"
        if target == "execution"
        else "evidence_ingress.authority_invalid"
    )
    with pytest.raises(ingress.PrivateEvidenceMaterialFailure, match=f"^{expected}$"):
        _read(case)
    assert len(case["events"]) == expected_exchanges


@pytest.mark.parametrize("target", ("signed_before_launch", "received_before_signed"))
def test_receipt_timestamp_ordering_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    case = _case(monkeypatch)
    receipt = case["receipt"]
    signed = binding._parse_policy(receipt.receipt_bytes)
    if target == "signed_before_launch":
        signed["observed_at"] = (
            case["record"].created_at - timedelta(seconds=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        receipt_bytes = binding._canonical(signed)
        object.__setattr__(receipt, "receipt_bytes", receipt_bytes)
        object.__setattr__(receipt, "receipt_sha256", binding._digest(receipt_bytes))
    else:
        signed_time = launch._time(signed["observed_at"], "test.invalid")
        object.__setattr__(receipt, "observed_at", signed_time - timedelta(seconds=1))
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.authority_invalid$",
    ):
        _read(case)
    assert not case["events"]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            wire.Response(
                EVIDENCE_BYTES["SHA256SUMS"], status=206, content_type="text/plain"
            ),
            "evidence_ingress.evidence_object_invalid",
        ),
        (
            wire.Response(
                EVIDENCE_BYTES["SHA256SUMS"], content_type="application/json"
            ),
            "evidence_ingress.evidence_object_invalid",
        ),
        (
            wire.Response(b"changed", content_type="text/plain"),
            "evidence_ingress.evidence_object_invalid",
        ),
        (
            wire.Response(
                EVIDENCE_BYTES["SHA256SUMS"],
                content_type="text/plain",
                content_length=False,
            ),
            "evidence_ingress.evidence_object_invalid",
        ),
        (
            wire.Response(
                EVIDENCE_BYTES["SHA256SUMS"],
                content_type="text/plain",
                extra_headers=(("Transfer-Encoding", "chunked"),),
            ),
            "evidence_ingress.evidence_object_invalid",
        ),
        (
            wire.Response(
                EVIDENCE_BYTES["SHA256SUMS"],
                content_type="text/plain",
                extra_headers=(("Content-Range", "bytes 0-1/2"),),
            ),
            "evidence_ingress.evidence_object_invalid",
        ),
        (
            wire.Response(
                EVIDENCE_BYTES["SHA256SUMS"],
                content_type="text/plain",
                url="https://attacker.invalid/redirect",
            ),
            "evidence_ingress.evidence_object_invalid",
        ),
    ],
)
def test_blob_response_framing_status_media_and_digest_are_strict(
    monkeypatch: pytest.MonkeyPatch,
    response: wire.Response,
    expected: str,
) -> None:
    responses = _blob_responses()
    responses[0] = response
    case = _case(monkeypatch, blob_responses=responses)
    with pytest.raises(ingress.PrivateEvidenceMaterialFailure, match=f"^{expected}$"):
        _read(case)
    assert len(case["blob"].calls) == 1
    assert not case["vault"].calls


@pytest.mark.parametrize(
    "encoded",
    (
        base64.urlsafe_b64encode(HMAC_KEY_BYTES).decode("ascii"),
        base64.urlsafe_b64encode(b"too-short").rstrip(b"=").decode("ascii"),
        "not+base64url",
        base64.urlsafe_b64encode(b"x" * 32).rstrip(b"=").decode("ascii"),
    ),
)
def test_hmac_secret_requires_canonical_unpadded_exact_decoded_material(
    monkeypatch: pytest.MonkeyPatch, encoded: str
) -> None:
    policy = policy_helpers._configured_policy()
    case = _case(monkeypatch, vault_body=_vault_body(policy, encoded))
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.hmac_secret_invalid$",
    ):
        _read(case)
    assert len(case["identity"].calls) == 2
    assert len(case["blob"].calls) == 7
    assert len(case["vault"].calls) == 1


def test_final_uri_and_secret_response_binding_are_strict_and_errors_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_text = "private-secret-that-must-not-leak"
    case = _case(monkeypatch)
    case["vault"].responses[0] = wire.Response(
        secret_text.encode(),
        url="https://attacker.invalid/redirect",
    )
    with pytest.raises(ingress.PrivateEvidenceMaterialFailure) as captured:
        _read(case)
    assert str(captured.value) == "evidence_ingress.hmac_secret_invalid"
    assert secret_text not in str(captured.value)
    assert secret_text not in repr(captured.value)


@pytest.mark.parametrize(
    ("status", "media_type", "wrong_id", "enabled"),
    (
        (500, "application/json", False, True),
        (200, "text/plain", False, True),
        (200, "application/json", True, True),
        (200, "application/json", False, False),
    ),
)
def test_hmac_response_status_media_id_and_enabled_are_strict(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    media_type: str,
    wrong_id: bool,
    enabled: bool,
) -> None:
    policy = policy_helpers._configured_policy()
    body = json.loads(_vault_body(policy))
    if wrong_id:
        body["id"] = "https://attacker.invalid/secrets/key/version"
    body["attributes"]["enabled"] = enabled
    case = _case(monkeypatch)
    case["vault"].responses[0] = wire.Response(
        json.dumps(body).encode(), status=status, content_type=media_type
    )
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.hmac_secret_invalid$",
    ):
        _read(case)
    assert case["events"] == [
        "identity",
        *("blob" for _ in range(7)),
        "identity",
        "vault",
    ]


def test_deadline_regression_refuses_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter((10.0, 10.0, 9.0))
    case = _case(monkeypatch, monotonic=lambda: next(values))
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.clock_invalid$",
    ):
        _read(case)
    assert not case["identity"].calls
    assert not case["blob"].calls
    assert not case["vault"].calls


def test_total_deadline_refuses_after_one_exchange_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter((10.0, 10.0, 10.0, 101.0))
    case = _case(monkeypatch, monotonic=lambda: next(values))
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.deadline$",
    ):
        _read(case)
    assert case["events"] == ["identity"]


@pytest.mark.parametrize(("stage", "expected_exchanges"), STAGE_EXCHANGES)
def test_trusted_clock_rejects_expiry_after_every_remote_stage(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    expected_exchanges: int,
) -> None:
    current: dict[str, Any] = {"instant": launch_helpers.VALIDATION_NOW}
    case = _case(monkeypatch, clock=lambda: current["instant"])
    _after_stage(
        case,
        stage,
        lambda: current.update(instant=case["record"].expires_at),
    )
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.launch_expired$",
    ):
        _read(case)
    assert len(case["events"]) == expected_exchanges


def test_trusted_clock_rejects_expiry_before_io_and_after_sealing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, Any] = {}
    expired = _case(monkeypatch, clock=lambda: holder["record"].expires_at)
    holder["record"] = expired["record"]
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.launch_expired$",
    ):
        _read(expired)
    assert not expired["events"]

    calls = 0
    sealed: dict[str, Any] = {}

    def expire_after_sealing() -> Any:
        nonlocal calls
        calls += 1
        return (
            sealed["record"].expires_at
            if calls >= 12
            else launch_helpers.VALIDATION_NOW
        )

    final = _case(monkeypatch, clock=expire_after_sealing)
    sealed["record"] = final["record"]
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.launch_expired$",
    ):
        _read(final)
    assert calls == 12
    assert len(final["events"]) == 10


def test_trusted_clock_regression_refuses_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter(
        (
            launch_helpers.VALIDATION_NOW,
            launch_helpers.VALIDATION_NOW - timedelta(seconds=1),
        )
    )
    case = _case(monkeypatch, clock=lambda: next(values))
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.trusted_clock_regressed$",
    ):
        _read(case)
    assert case["events"] == ["identity"]


def test_factory_accepts_only_exact_executable_policy() -> None:
    policy = policy_helpers._configured_policy()
    assert type(
        ingress.build_private_evidence_material_reader(
            policy, clock=lambda: launch_helpers.VALIDATION_NOW
        )
    ) is (ingress.ManagedIdentityPrivateEvidenceMaterialReader)
    policy["evidence_ingress"]["private_restore_evidence_container"]["retrieval"][
        "maximum_total_http_exchanges"
    ] = 11
    with pytest.raises(
        ingress.PrivateEvidenceMaterialFailure,
        match="^evidence_ingress.configuration_invalid$",
    ):
        ingress.build_private_evidence_material_reader(
            policy, clock=lambda: launch_helpers.VALIDATION_NOW
        )


def test_policy_pins_exact_dormant_reader_contract() -> None:
    policy = policy_helpers._policy()
    blob = policy["evidence_ingress"]["private_restore_evidence_container"]
    retrieval = blob["retrieval"]
    assert blob["reader_status"] == "dormant_implemented_unintegrated"
    assert tuple(retrieval["evidence_file_media_types"]) == tuple(
        blob["objects"]["exact_evidence_file_names"]
    )
    assert retrieval["maximum_attempts_per_object"] == 1
    assert retrieval["maximum_evidence_object_get_requests"] == 6
    assert retrieval["maximum_attestation_object_get_requests"] == 1
    assert retrieval["maximum_storage_token_requests"] == 1
    assert retrieval["maximum_vault_token_requests"] == 1
    assert retrieval["maximum_hmac_secret_get_requests"] == 1
    assert retrieval["maximum_total_http_exchanges"] == 10
    assert retrieval["exact_version_only"] is True
    assert retrieval["listing_permitted"] is False
    assert retrieval["polling_permitted"] is False
    assert retrieval["retry_permitted"] is False
    assert retrieval["redirects_permitted"] is False
    assert retrieval["ambient_credential_permitted"] is False
    assert retrieval["ambient_proxy_permitted"] is False
    assert retrieval["caller_selector_override_permitted"] is False
    assert retrieval["disk_materialization_permitted"] is False
    assert retrieval["trusted_utc_expiry_recheck"] == "before-and-after-each-stage"
    assert policy["activation_ready"] is False
    assert not any(policy["activation"].values())
