from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import bind_staging_restore_fixed_catalog_source_state as binding
from scripts import staging_restore_sanitizer as sanitizer
from scripts import staging_synthetic_inventory as inventory
from scripts import write_staging_controller_receipt_v2 as receipt_writer

from tests.infrastructure import (
    test_staging_controller_receipt_v2 as content_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2_builder as builder_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2_database_observation as database_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2_isolation_inventory as isolation_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2_writer as writer_helpers,
)
from tests.infrastructure import test_staging_restore_runtime_binding as runtime_helpers
from tests.infrastructure import test_staging_synthetic_inventory as inventory_helpers

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/bind_staging_restore_fixed_catalog_source_state.py"
POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-sanitizer-policy.json"
INVENTORY_POLICY = ROOT / "contracts/staging-rehearsal/v1/synthetic-data-policy.json"
NOW = builder_helpers.NOW
INVENTORY_OBSERVED_AT = "2026-09-05T12:00:00Z"
INVENTORY_EXPIRES_AT = "2026-09-05T12:05:00Z"
RealChain = tuple[dict[str, Any], bytes, Any, Any, Any, Any]


def _inventory_document() -> dict[str, Any]:
    policy = json.loads(INVENTORY_POLICY.read_bytes())
    policy_sha256 = hashlib.sha256(INVENTORY_POLICY.read_bytes()).hexdigest()
    provider = inventory_helpers.FakeProvider(inventory, policy, profile="pre")
    approved = inventory_helpers._approval(inventory, policy, policy_sha256, provider)
    trusted = policy["evidence"]["trusted_context"]
    provenance = {
        "repository": trusted["repository"],
        "protected_ref": trusted["protected_ref"],
        "workflow_path": trusted["workflow_path"],
        "workflow_sha": "c" * 40,
        "run_id": "33990000001",
        "run_attempt": "1",
        "event_name": "workflow_dispatch",
    }
    signer = {
        "issuer": trusted["issuer"],
        "subject": trusted["subject"],
        "repository": provenance["repository"],
        "ref": provenance["protected_ref"],
        "sha": provenance["workflow_sha"],
        "workflow_ref": (
            f"{provenance['repository']}/{provenance['workflow_path']}"
            f"@{provenance['protected_ref']}"
        ),
        "workflow_sha": provenance["workflow_sha"],
        "run_id": provenance["run_id"],
        "run_attempt": provenance["run_attempt"],
        "actor": trusted["founder_actor"],
        "triggering_actor": trusted["founder_actor"],
    }
    document = inventory.verify_inventory(
        policy=policy,
        policy_sha256=policy_sha256,
        approved=approved,
        profile_name="pre",
        source_sha="a" * 40,
        candidate_sha="b" * 40,
        approved_state_sha256=hashlib.sha256(
            inventory._canonical_json(approved)
        ).hexdigest(),
        hmac_key_version=inventory.SUPPORTED_HMAC_KEY_VERSION,
        observed_at=INVENTORY_OBSERVED_AT,
        expires_at=INVENTORY_EXPIRES_AT,
        provenance=provenance,
        signer_claims=signer,
        key=inventory_helpers.KEY,
        provider=cast(inventory.InventoryProvider, provider),
    )
    return document


def _seal(document: dict[str, Any]) -> bytes:
    unsigned = copy.deepcopy(document)
    unsigned.pop("receipt_sha256", None)
    document["receipt_sha256"] = hashlib.sha256(
        inventory._canonical_json(unsigned)
    ).hexdigest()
    return inventory._canonical_json(document)


@contextmanager
def _chain(
    inventory_receipt_bytes: bytes,
) -> Iterator[
    tuple[
        Any,
        Any,
        receipt_writer.WrittenControllerReceiptV2,
        sanitizer.SanitizerTargetBinding,
    ]
]:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(isolation_helpers, "_DATABASE_AUTHORITY_TEMPLATE", None)
    monkeypatch.setattr(database_helpers, "_AUTHORITY_PAIR_TEMPLATE", None)
    prior_subscription = content_helpers.SUBSCRIPTION
    source = json.loads(inventory_receipt_bytes)["source_resource"][
        "postgres_server_id"
    ]
    subscription = source.split("/")[2]
    groups = f"/subscriptions/{subscription}/resourceGroups"
    overrides = {
        "SUBSCRIPTION": subscription,
        "SOURCE_SERVER": source,
        "CONTROL_GROUP": f"{groups}/cosmaneura-staging-restore-control",
        "TARGET_GROUP": f"{groups}/cosmaneura-staging-rehearsal",
    }
    for name, value in overrides.items():
        monkeypatch.setattr(content_helpers, name, value)
    original_runtime = runtime_helpers._configured_policy

    def configured_runtime() -> dict[str, Any]:
        return json.loads(
            json.dumps(original_runtime()).replace(prior_subscription, subscription)
        )

    monkeypatch.setattr(runtime_helpers, "_configured_policy", configured_runtime)
    original_evidence = content_helpers._evidence
    receipt_sha256 = json.loads(inventory_receipt_bytes)["receipt_sha256"]

    def evidence() -> dict[str, Any]:
        value = original_evidence()
        value["inventory_receipt_sha256"] = f"sha256:{receipt_sha256}"
        return value

    monkeypatch.setattr(content_helpers, "_evidence", evidence)
    try:
        authority, built = builder_helpers._build(monkeypatch)
        writer, _identity, _blob = writer_helpers._instance(monkeypatch, built)
        written = writer.write(built_receipt=built)
        target_binder = sanitizer.SanitizerTargetBinder._from_reviewed_configuration(
            clock=lambda: NOW
        )
        target = target_binder.bind(written_receipt=written)
        yield authority, built, written, target
    finally:
        monkeypatch.undo()


@pytest.fixture(scope="module")
def real_chain() -> Iterator[RealChain]:
    document = _inventory_document()
    raw = _seal(document)
    with _chain(raw) as (authority, built, written, target):
        yield document, raw, authority, built, written, target


@pytest.fixture(scope="module")
def authenticated(
    real_chain: RealChain,
) -> Any:
    _document, raw, _authority, _built, _written, target = real_chain
    binder = binding.FixedCatalogSourceStateBinder._from_reviewed_configuration(
        clock=lambda: NOW
    )
    return binder.bind(
        target_binding=target,
        inventory_receipt_bytes=raw,
    )


def _binder(now: Any = NOW) -> binding.FixedCatalogSourceStateBinder:
    return binding.FixedCatalogSourceStateBinder._from_reviewed_configuration(
        clock=lambda: now
    )


def _reject(target: Any, raw: Any, code: str) -> None:
    with pytest.raises(binding.FixedCatalogSourceStateFailure, match=code):
        _binder().bind(
            target_binding=cast(Any, target),
            inventory_receipt_bytes=cast(Any, raw),
        )


def _set(document: dict[str, Any], path: str, value: Any) -> None:
    target = document
    parts = path.split(".")
    for name in parts[:-1]:
        target = target[name]
    target[parts[-1]] = value
