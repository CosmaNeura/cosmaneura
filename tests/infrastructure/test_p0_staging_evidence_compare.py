from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
EVIDENCE_SCRIPT = ROOT / "scripts" / "p0_staging_evidence.py"
COMPARE_SCRIPT = ROOT / "scripts" / "p0_staging_evidence_compare.py"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _modules() -> tuple[ModuleType, ModuleType]:
    evidence = _load_module("p0_staging_evidence", EVIDENCE_SCRIPT)
    compare = _load_module("p0_staging_evidence_compare", COMPARE_SCRIPT)
    return evidence, compare


def _empty_rows(evidence: ModuleType) -> dict[str, list[dict[str, Any]]]:
    return {spec.name: [] for spec in evidence.TABLE_SPECS}


def _expectations(compare: ModuleType) -> dict[str, Any]:
    return {
        **{name: 0 for name in compare.EXPECTED_P0_PATHS},
        **{name: [] for name in compare.EXPECTED_PLAN_PATHS},
    }


def _snapshots(
    evidence: ModuleType,
    *,
    pre_rows: dict[str, list[dict[str, Any]]],
    post_rows: dict[str, list[dict[str, Any]]],
    key: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    pre = evidence.build_snapshot(
        pre_rows,
        key,
        phase="pre",
        p0_schema_presence={table: False for table in evidence.P0_SCHEMA_TABLES},
    )
    post = evidence.build_snapshot(
        post_rows,
        key,
        phase="post",
        p0_schema_presence={table: True for table in evidence.P0_SCHEMA_TABLES},
    )
    return pre, post


def _write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_compare_accepts_authenticated_unchanged_business_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence, compare = _modules()
    key = b"k" * 32
    pre_rows = _empty_rows(evidence)
    post_rows = copy.deepcopy(pre_rows)
    pre, post = _snapshots(
        evidence,
        pre_rows=pre_rows,
        post_rows=post_rows,
        key=key,
    )
    key_path = tmp_path / "hmac.key"
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    pre_path = tmp_path / "pre.json"
    post_path = tmp_path / "post.json"
    expectations_path = tmp_path / "expectations.json"
    _write_private_json(pre_path, pre)
    _write_private_json(post_path, post)
    _write_private_json(expectations_path, _expectations(compare))

    assert (
        compare.main(
            [
                "--pre",
                str(pre_path),
                "--post",
                str(post_path),
                "--expectations",
                str(expectations_path),
                "--hmac-key-file",
                str(key_path),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == "P0 staging evidence comparison passed.\n"
    assert captured.err == ""

    expectations_path.write_text('{"duplicate":0,"duplicate":0}')
    expectations_path.chmod(0o600)
    assert (
        compare.main(
            [
                "--pre",
                str(pre_path),
                "--post",
                str(post_path),
                "--expectations",
                str(expectations_path),
                "--hmac-key-file",
                str(key_path),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "P0 staging evidence comparison failed.\n"


def test_compare_accepts_exact_additive_continuity_amendment() -> None:
    evidence, compare = _modules()
    key = b"k" * 32
    source_sha256 = "f" * 64
    applied_at = "2026-08-24T12:00:00+00:00"
    base_grant = {
        "id": "pilot_access_grant_base",
        "doctor_id_snapshot": "doctor_base",
        "revoked_at": None,
    }
    amendment_grant = {
        "id": "pilot_access_grant_amendment",
        "clerk_id": "user_private_amendment",
        "doctor_id_snapshot": "doctor_amendment",
        "practice_id_snapshot": None,
        "email_snapshot": "private-amendment@example.invalid",
        "email_sha256": evidence._plain_sha256("private-amendment@example.invalid"),
        "source_ref": "review:private-continuity",
        "source_sha256": source_sha256,
        "grant_plan_sha256": "0" * 64,
        "granted_by_ref": "operator:private",
        "grant_reason": "Reviewed operational omission",
        "granted_at": applied_at,
        "revoked_at": None,
    }
    amendment = {
        "id": "pilot_amendment_private",
        "environment": "staging",
        "clerk_issuer": "https://clerk.staging.invalid",
        "source_ref": "review:private-continuity",
        "manifest_sha256": source_sha256,
        "account_count": 1,
        "plan_sha256": "0" * 64,
        "applied_by_ref": "operator:private",
        "reason": "Reviewed operational omission",
        "applied_at": applied_at,
    }
    amendment_identity = {
        "id": "pilot_amendment_identity_private",
        "amendment_id": "pilot_amendment_private",
        "grant_id": "pilot_access_grant_amendment",
        "clerk_issuer": "https://clerk.staging.invalid",
        "clerk_subject": "user_private_amendment",
        "email_sha256": evidence._plain_sha256("private-amendment@example.invalid"),
        "doctor_id_snapshot": "doctor_amendment",
        "practice_id_snapshot": None,
        "safe_prelogin": False,
        "continuity_basis": "reviewed_operational_user_omission",
    }
    plan_sha256 = evidence._canonical_sha256(
        {
            "action": "pilot_access_continuity_amendment",
            "environment": amendment["environment"],
            "identities": [
                {
                    "continuity_basis": amendment_identity["continuity_basis"],
                    "doctor_id": amendment_identity["doctor_id_snapshot"],
                    "email_sha256": amendment_identity["email_sha256"],
                    "issuer_sha256": evidence._plain_sha256(
                        amendment_identity["clerk_issuer"]
                    ),
                    "practice_id": amendment_identity["practice_id_snapshot"],
                    "subject_sha256": evidence._plain_sha256(
                        amendment_identity["clerk_subject"]
                    ),
                }
            ],
            "manifest_sha256": amendment["manifest_sha256"],
            "operator_ref": amendment["applied_by_ref"],
            "reason": amendment["reason"],
            "schema_version": 1,
            "source_ref": amendment["source_ref"],
        }
    )
    amendment_grant["grant_plan_sha256"] = plan_sha256
    amendment["plan_sha256"] = plan_sha256
    pre_rows = _empty_rows(evidence)
    pre_rows["pilot_access_grandfather_grant"] = [base_grant]
    post_rows = copy.deepcopy(pre_rows)
    post_rows["pilot_access_grandfather_grant"].append(amendment_grant)
    post_rows["pilot_access_continuity_amendment"] = [amendment]
    post_rows["pilot_access_continuity_amendment_identity"] = [amendment_identity]
    pre, post = _snapshots(
        evidence,
        pre_rows=pre_rows,
        post_rows=post_rows,
        key=key,
    )
    expected = _expectations(compare)
    expected.update(
        {
            "continuity_amendment_rows": 1,
            "continuity_declared_accounts": 1,
            "continuity_identity_rows": 1,
            "continuity_amendment_grant_rows": 1,
            "continuity_amendment_grant_active_count": 1,
            "continuity_amendment_grant_doctor_snapshot_count": 1,
            "continuity_amendment_grant_prelogin_count": 0,
            "continuity_amendment_grant_revoked_count": 0,
            "continuity_plan_sha256s": [plan_sha256],
            "continuity_grant_plan_sha256s": [plan_sha256],
        }
    )

    compare.compare_snapshots(pre, post, expected, key)


def test_compare_rejects_business_drift_tampering_and_orphan_grants() -> None:
    evidence, compare = _modules()
    key = b"k" * 32
    pre_rows = _empty_rows(evidence)
    post_rows = copy.deepcopy(pre_rows)
    pre, post = _snapshots(
        evidence,
        pre_rows=pre_rows,
        post_rows=post_rows,
        key=key,
    )
    expected = _expectations(compare)

    drifted_rows = copy.deepcopy(post_rows)
    drifted_rows["baa_acceptance"] = [{"id": "private-acceptance"}]
    _, drifted = _snapshots(
        evidence,
        pre_rows=pre_rows,
        post_rows=drifted_rows,
        key=key,
    )
    with pytest.raises(compare.ComparisonFailure, match="business state"):
        compare.compare_snapshots(pre, drifted, expected, key)

    tampered = copy.deepcopy(post)
    tampered["sections"]["baa"]["acceptances"]["count"] = 1
    with pytest.raises(compare.ComparisonFailure, match="authentication"):
        compare.compare_snapshots(pre, tampered, expected, key)

    orphaned_rows = copy.deepcopy(post_rows)
    orphaned_rows["pilot_access_continuity_amendment_identity"] = [
        {"grant_id": "pilot_access_grant_missing", "safe_prelogin": False}
    ]
    with pytest.raises(evidence.EvidenceFailure, match="linkage"):
        evidence.build_snapshot(orphaned_rows, key, phase="post")
