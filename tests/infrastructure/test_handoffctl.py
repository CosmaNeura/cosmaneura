import copy
import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
HANDOFFCTL = ROOT / ".github/scripts/handoffctl.py"
SCHEMA_DIRECTORY = ROOT / ".github/schemas"

BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40
NEW_HEAD_SHA = "d" * 40
POLICY_SHA = "c" * 40
INPUT_DIGEST = f"sha256:{'0' * 64}"
AGENTS = {
    "deployment_assessment": "deploy-navigator",
    "migration_assessment": "migration-sentinel",
    "release_assessment": "release-medic",
    "security_assessment": "security-vulnerability-sentinel",
}
FOUNDRY_AGENT_VERSIONS = {
    "deployment_assessment": "4",
    "migration_assessment": "1",
    "release_assessment": "1",
    "security_assessment": "2",
}
SCANNERS = ("CodeQL/SAST", "dependency", "secret", "IaC", "SBOM", "container")


def _evidence_policy() -> dict[str, object]:
    scanner_coverage = [
        {
            "locator": (
                "scanner-coverage:"
                + "".join(
                    character if character.isalnum() else "-"
                    for character in scanner.casefold()
                ).strip("-")
            ),
            "scanner": scanner,
            "status": "unavailable",
        }
        for scanner in SCANNERS
    ]
    manifest = [
        {
            "locator": "pull-request:101",
            "availability": "available",
            "required_for_deployment": True,
        },
        {
            "locator": ".github/workflows/build_and_publish.yml",
            "availability": "available",
            "required_for_deployment": True,
        },
        {
            "locator": "check-run:901",
            "availability": "available",
            "required_for_deployment": True,
        },
        {
            "locator": "check:synthetic-health",
            "availability": "available",
            "required_for_deployment": False,
        },
        {
            "locator": "semantic-source:unavailable",
            "availability": "unavailable",
            "required_for_deployment": False,
        },
        {
            "locator": "migration-rehearsal:unavailable",
            "availability": "unavailable",
            "required_for_deployment": False,
        },
        *[
            {
                "locator": scanner["locator"],
                "availability": "unavailable",
                "required_for_deployment": False,
            }
            for scanner in scanner_coverage
        ],
    ]
    manifest.sort(key=lambda item: item["locator"])
    return {
        "allowed_action_evidence_locators": [item["locator"] for item in manifest],
        "evidence_manifest": manifest,
        "pull_request": {
            "locator": "pull-request:101",
            "state": "open",
            "draft": False,
        },
        "checks": [
            {
                "locator": "check-run:901",
                "check_run_id": 901,
                "name": "tests",
                "required": True,
                "status": "completed",
                "conclusion": "failure",
            }
        ],
        "scanner_coverage": scanner_coverage,
        "semantic_source": {
            "locator": "semantic-source:unavailable",
            "status": "unavailable",
        },
        "migration_rehearsal": {
            "locator": "migration-rehearsal:unavailable",
            "status": "unavailable",
        },
    }


def _seal_assessment(document: dict[str, object]) -> dict[str, object]:
    preimage = copy.deepcopy(document)
    preimage["integrity"].pop("artifact_digest", None)
    encoded = json.dumps(
        preimage,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    document["integrity"]["artifact_digest"] = (
        f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    )
    return document


def _action(prefix: str) -> dict[str, object]:
    action: dict[str, object] = {
        "id": f"{prefix}-001",
        "category": "deployment.readiness",
        "severity": "medium",
        "confidence": 0.95,
        "blocking": True,
        "state": "open",
        "title": "Required evidence is missing",
        "evidence": [
            {
                "source": "repository",
                "locator": ".github/workflows/build_and_publish.yml",
                "line_start": 10,
                "line_end": 12,
                "claim": "The required check is not declared.",
            }
        ],
        "required_change": "Add the required check and capture its result.",
        "non_goals": ["Do not change production resources."],
        "verification": [
            {
                "test": "Run the workflow against the assessed commit.",
                "expected": "The required check succeeds.",
            }
        ],
        "owner_role": "implementation-agent",
    }
    if prefix == "SEC":
        action.update(
            {
                "category": "authorization.tenant-isolation",
                "severity": "low",
                "blocking": False,
                "classification": "potential",
                "cwe": "CWE-862",
                "attack_path": "A request may reach a record without an ownership filter.",
                "impact": "A tenant boundary could be weakened.",
            }
        )
    return action


def _assessment(
    kind: str = "deployment_assessment",
    *,
    generated_at: str = "2026-08-25T12:00:00Z",
    artifact_digit: str = "1",
) -> dict[str, object]:
    prefix = {
        "deployment_assessment": "DEP",
        "migration_assessment": "MIG",
        "release_assessment": "REL",
        "security_assessment": "SEC",
    }[kind]
    payloads: dict[str, dict[str, object]] = {
        "deployment_assessment": {
            "target_environment": "staging",
            "required_checks": [
                {
                    "name": "tests",
                    "status": "failure",
                    "evidence_locator": "check-run:901",
                }
            ],
            "migration_review_required": False,
        },
        "migration_assessment": {
            "migration_paths": ["migrations/versions/example.py"],
            "rehearsal_status": "unavailable",
            "expand_contract_compliant": True,
        },
        "release_assessment": {
            "deployed_sha": HEAD_SHA,
            "image_digest": f"sha256:{'f' * 64}",
            "revision": "core-staging--example",
            "health_checks": [
                {
                    "name": "synthetic-health",
                    "status": "success",
                    "evidence_locator": "check:synthetic-health",
                }
            ],
        },
        "security_assessment": {
            "review_scope": ["application diff", "dependency manifests"],
            "scanner_inputs": [
                {
                    "name": scanner,
                    "status": "unavailable",
                    "evidence_locator": (
                        "scanner-coverage:"
                        + "".join(
                            character if character.isalnum() else "-"
                            for character in scanner.casefold()
                        ).strip("-")
                    ),
                }
                for scanner in SCANNERS
            ],
            "live_target_testing": False,
        },
    }
    statuses = {
        "deployment_assessment": "HOLD",
        "migration_assessment": "HOLD",
        "release_assessment": "INCOMPLETE",
        "security_assessment": "INCOMPLETE",
    }
    actions = [_action(prefix)]
    document: dict[str, object] = {
        "schema_version": "cosmaneura.agent-handoff.v1",
        "kind": kind,
        "handoff_id": f"{kind}:CosmasCode/cosmaneura:{HEAD_SHA}:run-101",
        "producer": {
            "agent": AGENTS[kind],
            "policy_version": "1.0.0",
            "foundry_agent_version": FOUNDRY_AGENT_VERSIONS[kind],
            "model": "gpt-5.6-sol",
            "policy_sha": POLICY_SHA,
        },
        "subject": {
            "repository": "CosmasCode/cosmaneura",
            "pull_request": 101,
            "base_sha": BASE_SHA,
            "head_sha": HEAD_SHA,
            "workflow_run_id": 202,
            "environment": "staging",
        },
        "coverage": {
            "included": ["diff", "CI"],
            "omitted": ["production application telemetry"],
            "evidence_fresh_until": "2099-08-25T13:00:00Z",
        },
        "decision": {
            "status": statuses[kind],
            "risk_tier": "T1",
            "summary": "Assessment is bound to the exact commit.",
        },
        "payload": payloads[kind],
        "actions": actions,
        "constraints": {
            "must_not": ["Modify production."],
            "approvals_required": [],
        },
        "integrity": {
            "generated_at": generated_at,
            "input_digest": f"sha256:{artifact_digit * 64}",
            "artifact_digest": f"sha256:{'0' * 64}",
        },
    }
    return _seal_assessment(document)


def _resolution() -> dict[str, object]:
    source = _assessment("security_assessment")
    return {
        "schema_version": "cosmaneura.agent-resolution.v1",
        "handoff_id": f"security:CosmasCode/cosmaneura:{HEAD_SHA}:run-101",
        "assessment": {
            "repository": "CosmasCode/cosmaneura",
            "head_sha": HEAD_SHA,
            "artifact_digest": source["integrity"]["artifact_digest"],
        },
        "resolver": {
            "agent": "implementation-agent",
            "agent_version": "1.0.0",
        },
        "new_head_sha": NEW_HEAD_SHA,
        "resolutions": [
            {
                "action_id": "SEC-001",
                "status": "fixed",
                "explanation": "Tenant ownership is now enforced in the query.",
                "evidence": [
                    {
                        "locator": "tests/services/practice_access_test.py",
                        "claim": "Cross-practice access returns 404.",
                    }
                ],
            }
        ],
        "integrity": {"generated_at": "2026-08-25T12:30:00Z"},
    }


def _draft(kind: str = "deployment_assessment") -> dict[str, object]:
    assessment = _assessment(kind)
    return {
        "draft_version": "cosmaneura.agent-assessment-draft.v1",
        "kind": assessment["kind"],
        "coverage": assessment["coverage"],
        "decision": assessment["decision"],
        "payload": assessment["payload"],
        "actions": assessment["actions"],
        "constraints": assessment["constraints"],
    }


def _context(kind: str = "deployment_assessment") -> dict[str, object]:
    assessment = _assessment(kind)
    return {
        "context_version": "cosmaneura.agent-broker-context.v1",
        "assessment_kind": kind,
        "producer": {
            "agent": AGENTS[kind],
            "policy_version": "1.0.0",
            "foundry_agent_version": FOUNDRY_AGENT_VERSIONS[kind],
            "model": "gpt-5.6-sol",
        },
        "policy_sha": POLICY_SHA,
        "subject": assessment["subject"],
        "risk_tier_floor": "T1",
        "generated_at": "2026-08-25T12:00:00Z",
        "evidence_fresh_until": "2026-08-25T14:00:00Z",
        "evidence_policy": _evidence_policy(),
        "input_digest": INPUT_DIGEST,
    }


def _write(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _run(*arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HANDOFFCTL), *(str(value) for value in arguments)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_canonical_schemas_are_valid_json() -> None:
    assert {
        path.name for path in SCHEMA_DIRECTORY.glob("cosmaneura-*.schema.json")
    } == {
        "cosmaneura-agent-broker-context-v1.schema.json",
        "cosmaneura-agent-handoff-v1.schema.json",
        "cosmaneura-agent-resolution-v1.schema.json",
        "cosmaneura-agent-trusted-policy-v1.schema.json",
    }
    for path in SCHEMA_DIRECTORY.glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["$schema"] == (
            "https://json-schema.org/draft/2020-12/schema"
        )


@pytest.mark.parametrize(
    "kind",
    [
        "deployment_assessment",
        "migration_assessment",
        "release_assessment",
        "security_assessment",
    ],
)
def test_validate_accepts_each_assessment_kind(tmp_path: Path, kind: str) -> None:
    path = _write(tmp_path / f"{kind}.json", _assessment(kind))

    result = _run("validate", path, "--contract", "assessment")

    assert result.returncode == 0, result.stderr
    assert "VALID assessment" in result.stdout


def test_validate_accepts_resolution_receipt(tmp_path: Path) -> None:
    path = _write(tmp_path / "resolution.json", _resolution())

    result = _run("validate", path, "--contract", "resolution")

    assert result.returncode == 0, result.stderr
    assert "VALID resolution" in result.stdout


def test_validate_recomputes_and_rejects_tampered_artifact_digest(
    tmp_path: Path,
) -> None:
    document = _assessment()
    document["decision"]["summary"] = "Content changed after broker stamping."
    path = _write(tmp_path / "tampered.json", document)

    result = _run("validate", path, "--contract", "assessment")

    assert result.returncode == 4
    assert "artifact_digest: does not match the canonical assessment content" in (
        result.stderr
    )


def test_validate_rejects_well_formed_but_incorrect_artifact_digest(
    tmp_path: Path,
) -> None:
    document = _assessment()
    document["integrity"]["artifact_digest"] = f"sha256:{'f' * 64}"
    path = _write(tmp_path / "wrong-digest.json", document)

    result = _run("validate", path)

    assert result.returncode == 4
    assert "artifact_digest: does not match" in result.stderr


def test_security_pass_with_findings_rejects_any_blocking_action(
    tmp_path: Path,
) -> None:
    document = _assessment("security_assessment")
    document["decision"]["status"] = "PASS_WITH_FINDINGS"
    document["actions"][0]["severity"] = "high"
    document["actions"][0]["blocking"] = True
    _seal_assessment(document)
    path = _write(tmp_path / "blocking-security-pass.json", document)

    result = _run("validate", path, "--contract", "assessment")

    assert result.returncode == 4
    assert "PASS_WITH_FINDINGS conflicts with blocking actions" in result.stderr


def test_resolution_can_be_verified_against_exact_assessment(tmp_path: Path) -> None:
    assessment = _assessment("security_assessment")
    assessment_path = _write(tmp_path / "assessment.json", assessment)
    receipt = _resolution()
    receipt["handoff_id"] = assessment["handoff_id"]
    receipt_path = _write(tmp_path / "resolution.json", receipt)

    result = _run("validate", receipt_path, "--assessment", assessment_path)

    assert result.returncode == 0, result.stderr


def test_resolution_binding_rejects_wrong_artifact_and_missing_actions(
    tmp_path: Path,
) -> None:
    assessment = _assessment("security_assessment")
    assessment_path = _write(tmp_path / "assessment.json", assessment)
    receipt = _resolution()
    receipt["handoff_id"] = assessment["handoff_id"]
    receipt["assessment"]["artifact_digest"] = f"sha256:{'9' * 64}"
    receipt["resolutions"] = []
    receipt_path = _write(tmp_path / "resolution.json", receipt)

    result = _run("validate", receipt_path, "--assessment", assessment_path)

    assert result.returncode == 4
    assert "does not match the source assessment" in result.stderr
    assert "missing resolution for SEC-001" in result.stderr


def test_failed_release_can_precede_image_creation(tmp_path: Path) -> None:
    document = _assessment("release_assessment")
    document["decision"]["status"] = "FAILED"
    document["payload"]["deployed_sha"] = None
    document["payload"]["image_digest"] = None
    _seal_assessment(document)
    path = _write(tmp_path / "failed-release.json", document)

    result = _run("validate", path)

    assert result.returncode == 0, result.stderr


def test_healthy_release_requires_deployed_identity(tmp_path: Path) -> None:
    document = _assessment("release_assessment")
    document["decision"]["status"] = "HEALTHY"
    document["payload"]["deployed_sha"] = None
    document["payload"]["image_digest"] = None
    path = _write(tmp_path / "invalid-healthy-release.json", document)

    result = _run("validate", path)

    assert result.returncode == 4
    assert "expected string" in result.stderr


@pytest.mark.parametrize(
    "mutation,expected_error",
    [
        (lambda value: value.update({"unexpected": True}), "additional property"),
        (
            lambda value: value["actions"].append(copy.deepcopy(value["actions"][0])),
            "action IDs must be unique",
        ),
        (
            lambda value: value["actions"][0].update({"state": "fixed"}),
            "expected constant 'open'",
        ),
        (
            lambda value: value.update({"handoff_id": "deployment:unbound"}),
            "complete subject.head_sha",
        ),
    ],
)
def test_validate_fails_closed_on_unsafe_assessment_shape(
    tmp_path: Path, mutation: object, expected_error: str
) -> None:
    document = _assessment()
    mutation(document)
    path = _write(tmp_path / "invalid.json", document)

    result = _run("validate", path)

    assert result.returncode == 4
    assert expected_error in result.stderr


def test_validate_rejects_non_json_numeric_constants(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{"confidence": NaN}', encoding="utf-8")

    result = _run("validate", path)

    assert result.returncode == 3
    assert "non-JSON numeric constant" in result.stderr


def test_validate_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema_version":"cosmaneura.agent-handoff.v1",'
        '"schema_version":"cosmaneura.agent-resolution.v1"}',
        encoding="utf-8",
    )

    result = _run("validate", path)

    assert result.returncode == 3
    assert "duplicate object key 'schema_version'" in result.stderr


def test_summarize_emits_worker_oriented_action_ids(tmp_path: Path) -> None:
    path = _write(tmp_path / "assessment.json", _assessment())

    result = _run("summarize", path)

    assert result.returncode == 0, result.stderr
    assert f"head_sha={HEAD_SHA}" in result.stdout
    assert "status=HOLD" in result.stdout
    assert "actions_blocking=1" in result.stdout
    assert (
        "action=DEP-001\tmedium\tBLOCKING\tRequired evidence is missing"
        in result.stdout
    )


def test_fetch_local_returns_newest_exact_sha_and_ignores_stale_sha(
    tmp_path: Path,
) -> None:
    artifact_directory = tmp_path / "artifacts"
    artifact_directory.mkdir()
    old = _assessment(generated_at="2026-08-25T11:00:00Z", artifact_digit="1")
    latest = _assessment(generated_at="2026-08-25T12:00:00Z", artifact_digit="2")
    other_sha = _assessment(generated_at="2026-08-25T13:00:00Z", artifact_digit="3")
    other_sha["subject"]["head_sha"] = NEW_HEAD_SHA
    other_sha["handoff_id"] = str(other_sha["handoff_id"]).replace(
        HEAD_SHA, NEW_HEAD_SHA
    )
    _seal_assessment(other_sha)
    _write(artifact_directory / "old.json", old)
    _write(artifact_directory / "latest.json", latest)
    _write(artifact_directory / "other-sha.json", other_sha)
    (artifact_directory / "unrelated.json").write_text("not json", encoding="utf-8")

    result = _run(
        "fetch-local",
        artifact_directory,
        "--repository",
        "CosmasCode/cosmaneura",
        "--sha",
        HEAD_SHA,
        "--kind",
        "deployment_assessment",
    )

    assert result.returncode == 0, result.stderr
    selected = json.loads(result.stdout)
    assert (
        selected["integrity"]["artifact_digest"]
        == latest["integrity"]["artifact_digest"]
    )
    assert selected["subject"]["head_sha"] == HEAD_SHA


def test_fetch_local_has_distinct_not_found_exit_code(tmp_path: Path) -> None:
    result = _run(
        "fetch-local",
        tmp_path,
        "--repository",
        "CosmasCode/cosmaneura",
        "--sha",
        HEAD_SHA,
    )

    assert result.returncode == 5
    assert "NOT_FOUND" in result.stderr


def test_fetch_local_rejects_expired_assessment(tmp_path: Path) -> None:
    document = _assessment(generated_at="2020-01-01T00:00:00Z")
    document["coverage"]["evidence_fresh_until"] = "2020-01-01T01:00:00Z"
    _seal_assessment(document)
    _write(tmp_path / "expired.json", document)

    result = _run(
        "fetch-local",
        tmp_path,
        "--repository",
        "CosmasCode/cosmaneura",
        "--sha",
        HEAD_SHA,
    )

    assert result.returncode == 4
    assert "assessment is expired and cannot be consumed" in result.stderr


def test_resolution_rejects_duplicate_action_ids(tmp_path: Path) -> None:
    document = _resolution()
    document["resolutions"].append(copy.deepcopy(document["resolutions"][0]))
    path = _write(tmp_path / "invalid-resolution.json", document)

    result = _run("validate", path)

    assert result.returncode == 4
    assert "action IDs must be unique" in result.stderr


@pytest.mark.parametrize("kind", list(AGENTS))
def test_stamp_creates_valid_canonical_assessment_for_each_kind(
    tmp_path: Path, kind: str
) -> None:
    draft_path = _write(tmp_path / "draft.json", _draft(kind))
    context_path = _write(tmp_path / "context.json", _context(kind))
    output_path = tmp_path / "handoff.json"

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        output_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"STAMPED {output_path} {kind} ")
    handoff = json.loads(output_path.read_text(encoding="utf-8"))
    assert "draft_version" not in handoff
    assert handoff["producer"] == {
        "agent": AGENTS[kind],
        "policy_version": "1.0.0",
        "foundry_agent_version": FOUNDRY_AGENT_VERSIONS[kind],
        "model": "gpt-5.6-sol",
        "policy_sha": POLICY_SHA,
    }
    assert handoff["subject"]["head_sha"] == HEAD_SHA
    assert HEAD_SHA in handoff["handoff_id"]
    preimage = copy.deepcopy(handoff)
    artifact_digest = preimage["integrity"].pop("artifact_digest")
    encoded = json.dumps(
        preimage,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert artifact_digest == f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".handoff.json.*.tmp"))
    assert _run("validate", output_path).returncode == 0


def test_stamp_overwrites_model_freshness_with_trusted_broker_deadline(
    tmp_path: Path,
) -> None:
    draft = _draft()
    draft["coverage"]["evidence_fresh_until"] = "2199-01-01T00:00:00Z"
    context = _context()
    context["evidence_fresh_until"] = "2026-08-25T13:00:00Z"
    draft_path = _write(tmp_path / "draft.json", draft)
    context_path = _write(tmp_path / "context.json", context)
    output_path = tmp_path / "handoff.json"

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        output_path,
    )

    assert result.returncode == 0, result.stderr
    handoff = json.loads(output_path.read_text(encoding="utf-8"))
    assert (
        handoff["coverage"]["evidence_fresh_until"] == (context["evidence_fresh_until"])
    )


def test_context_accepts_two_hour_freshness_boundary_and_rejects_longer(
    tmp_path: Path,
) -> None:
    context = _context()
    accepted_path = _write(tmp_path / "accepted-context.json", context)
    accepted = _run("validate", accepted_path, "--contract", "context")

    context["evidence_fresh_until"] = "2026-08-25T14:00:01Z"
    rejected_path = _write(tmp_path / "rejected-context.json", context)
    rejected = _run("validate", rejected_path, "--contract", "context")

    assert accepted.returncode == 0, accepted.stderr
    assert rejected.returncode == 4
    assert "must be no more than 2 hours" in rejected.stderr

    context["evidence_fresh_until"] = "2026-08-25T11:59:59Z"
    before_path = _write(tmp_path / "before-context.json", context)
    before = _run("validate", before_path, "--contract", "context")
    assert before.returncode == 4
    assert "must not precede trusted generated_at" in before.stderr


def test_stamp_rejects_action_and_payload_locators_outside_trusted_manifest(
    tmp_path: Path,
) -> None:
    draft = _draft()
    draft["actions"][0]["evidence"][0]["locator"] = "invented:evidence"
    draft["payload"]["required_checks"][0]["evidence_locator"] = "invented:check"
    draft_path = _write(tmp_path / "draft.json", draft)
    context_path = _write(tmp_path / "context.json", _context())

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        tmp_path / "handoff.json",
    )

    assert result.returncode == 4
    assert result.stderr.count("not present in the trusted evidence manifest") == 2


def test_context_rejects_allowlist_not_exactly_backed_by_manifest(
    tmp_path: Path,
) -> None:
    context = _context()
    context["evidence_policy"]["allowed_action_evidence_locators"].append(
        "unmanifested:evidence"
    )
    path = _write(tmp_path / "context.json", context)

    result = _run("validate", path, "--contract", "context")

    assert result.returncode == 4
    assert "must exactly match the evidence_manifest locators" in result.stderr


def test_deployment_ready_requires_open_pr_green_checks_and_available_evidence(
    tmp_path: Path,
) -> None:
    draft = _draft()
    draft["decision"]["status"] = "READY"
    draft["decision"]["risk_tier"] = "T0"
    draft["actions"] = []
    draft["payload"]["required_checks"][0]["status"] = "success"
    context = _context()
    context["risk_tier_floor"] = "T0"
    context["evidence_policy"]["checks"][0]["conclusion"] = "success"

    good_draft = _write(tmp_path / "good-draft.json", draft)
    good_context = _write(tmp_path / "good-context.json", context)
    accepted = _run(
        "stamp",
        "--draft",
        good_draft,
        "--context",
        good_context,
        "--output",
        tmp_path / "accepted.json",
    )

    context["evidence_policy"]["pull_request"]["draft"] = True
    context["evidence_policy"]["checks"][0]["status"] = "in_progress"
    context["evidence_policy"]["checks"][0]["conclusion"] = None
    required_item = next(
        item
        for item in context["evidence_policy"]["evidence_manifest"]
        if item["locator"] == ".github/workflows/build_and_publish.yml"
    )
    required_item["availability"] = "unavailable"
    blocked_context = _write(tmp_path / "blocked-context.json", context)
    rejected = _run(
        "stamp",
        "--draft",
        good_draft,
        "--context",
        blocked_context,
        "--output",
        tmp_path / "rejected.json",
    )

    assert accepted.returncode == 0, accepted.stderr
    assert rejected.returncode == 4
    assert "READY requires an open, non-draft pull request" in rejected.stderr
    assert "terminal success for every trusted required check" in rejected.stderr
    assert "deployment-required evidence to be available" in rejected.stderr


def test_deployment_ready_rejects_closed_pull_request(tmp_path: Path) -> None:
    draft = _draft()
    draft["decision"]["status"] = "READY"
    draft["decision"]["risk_tier"] = "T0"
    draft["actions"] = []
    draft["payload"]["required_checks"][0]["status"] = "success"
    context = _context()
    context["risk_tier_floor"] = "T0"
    context["evidence_policy"]["pull_request"]["state"] = "closed"
    context["evidence_policy"]["checks"][0]["conclusion"] = "success"

    result = _run(
        "stamp",
        "--draft",
        _write(tmp_path / "draft.json", draft),
        "--context",
        _write(tmp_path / "context.json", context),
        "--output",
        tmp_path / "handoff.json",
    )

    assert result.returncode == 4
    assert "READY requires an open, non-draft pull request" in result.stderr


def test_deployment_ready_rejects_unattested_observed_checks(tmp_path: Path) -> None:
    draft = _draft()
    draft["decision"]["status"] = "READY"
    draft["decision"]["risk_tier"] = "T0"
    draft["actions"] = []
    draft["payload"]["required_checks"] = []
    context = _context()
    context["risk_tier_floor"] = "T0"
    context["evidence_policy"]["checks"][0]["required"] = False

    result = _run(
        "stamp",
        "--draft",
        _write(tmp_path / "draft.json", draft),
        "--context",
        _write(tmp_path / "context.json", context),
        "--output",
        tmp_path / "handoff.json",
    )

    assert result.returncode == 4
    assert "READY requires at least one trusted required check" in result.stderr


@pytest.mark.parametrize("tier", ["T1", "T2"])
def test_deployment_ready_is_disabled_for_tier_one_and_two_without_trusted_controls(
    tmp_path: Path, tier: str
) -> None:
    draft = _draft()
    draft["decision"]["status"] = "READY"
    draft["decision"]["risk_tier"] = tier
    draft["actions"] = []
    draft["payload"]["required_checks"][0]["status"] = "success"
    context = _context()
    context["risk_tier_floor"] = tier
    context["evidence_policy"]["checks"][0]["conclusion"] = "success"
    draft_path = _write(tmp_path / "draft.json", draft)
    context_path = _write(tmp_path / "context.json", context)

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        tmp_path / "handoff.json",
    )

    assert result.returncode == 4
    assert "READY is unavailable for T1/T2" in result.stderr


def test_migration_ready_requires_semantic_source_and_passed_rehearsal(
    tmp_path: Path,
) -> None:
    draft = _draft("migration_assessment")
    draft["decision"]["status"] = "READY"
    draft["actions"] = []
    draft_path = _write(tmp_path / "draft.json", draft)
    context_path = _write(tmp_path / "context.json", _context("migration_assessment"))

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        tmp_path / "handoff.json",
    )

    assert result.returncode == 4
    assert "trusted passed migration rehearsal" in result.stderr
    assert "trusted semantic source evidence" in result.stderr


@pytest.mark.parametrize("status", ["PASS_WITH_FINDINGS", "NO_FINDINGS_OBSERVED"])
def test_security_green_decisions_require_semantic_source_and_all_scanners(
    tmp_path: Path, status: str
) -> None:
    draft = _draft("security_assessment")
    draft["decision"]["status"] = status
    if status == "NO_FINDINGS_OBSERVED":
        draft["actions"] = []
    draft_path = _write(tmp_path / "draft.json", draft)
    context_path = _write(tmp_path / "context.json", _context("security_assessment"))

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        tmp_path / "handoff.json",
    )

    assert result.returncode == 4
    assert "trusted semantic source evidence" in result.stderr
    assert "every trusted scanner coverage status" in result.stderr


def test_stamp_rejects_model_scanner_status_that_differs_from_broker(
    tmp_path: Path,
) -> None:
    draft = _draft("security_assessment")
    draft["payload"]["scanner_inputs"][0]["status"] = "available"
    draft_path = _write(tmp_path / "draft.json", draft)
    context_path = _write(tmp_path / "context.json", _context("security_assessment"))

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        tmp_path / "handoff.json",
    )

    assert result.returncode == 4
    assert "must exactly match trusted scanner coverage" in result.stderr


def test_release_without_pr_is_supported_but_healthy_is_fail_closed(
    tmp_path: Path,
) -> None:
    context = _context("release_assessment")
    context["subject"].pop("pull_request")
    context["evidence_policy"]["pull_request"] = None
    context["evidence_policy"]["allowed_action_evidence_locators"].remove(
        "pull-request:101"
    )
    context["evidence_policy"]["evidence_manifest"] = [
        item
        for item in context["evidence_policy"]["evidence_manifest"]
        if item["locator"] != "pull-request:101"
    ]
    context_path = _write(tmp_path / "context.json", context)
    assert _run("validate", context_path, "--contract", "context").returncode == 0

    draft = _draft("release_assessment")
    draft["decision"]["status"] = "HEALTHY"
    draft["actions"] = []
    draft_path = _write(tmp_path / "draft.json", draft)
    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        tmp_path / "handoff.json",
    )

    assert result.returncode == 4
    assert "HEALTHY is disabled until broker-owned release" in result.stderr


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (
            lambda value: value["producer"].pop("policy_version"),
            "policy_version: required property is missing",
        ),
        (
            lambda value: value["producer"].update({"agent_version": "1.0.0"}),
            "agent_version: additional property is not allowed",
        ),
        (
            lambda value: value["producer"].pop("foundry_agent_version"),
            "foundry_agent_version: required property is missing",
        ),
        (
            lambda value: value["producer"].update({"foundry_agent_version": "1.0.0"}),
            "foundry_agent_version: does not match required pattern",
        ),
        (
            lambda value: value.pop("evidence_fresh_until"),
            "evidence_fresh_until: required property is missing",
        ),
        (
            lambda value: value.pop("evidence_policy"),
            "evidence_policy: required property is missing",
        ),
    ],
)
def test_stamp_requires_policy_foundry_versions_and_trusted_freshness(
    tmp_path: Path, mutation: object, expected_error: str
) -> None:
    context = _context()
    mutation(context)
    draft_path = _write(tmp_path / "draft.json", _draft())
    context_path = _write(tmp_path / "context.json", context)

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        tmp_path / "handoff.json",
    )

    assert result.returncode == 4
    assert expected_error in result.stderr


def test_stamp_rejects_model_supplied_trusted_fields_without_touching_output(
    tmp_path: Path,
) -> None:
    draft = _draft()
    draft["subject"] = {"head_sha": "f" * 40}
    draft["artifact_digest"] = f"sha256:{'f' * 64}"
    draft_path = _write(tmp_path / "draft.json", draft)
    context_path = _write(tmp_path / "context.json", _context())
    output_path = tmp_path / "handoff.json"
    output_path.write_text("preserve-existing-output", encoding="utf-8")

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        output_path,
    )

    assert result.returncode == 4
    assert "model draft must not supply trusted field" in result.stderr
    assert output_path.read_text(encoding="utf-8") == "preserve-existing-output"


def test_stamp_rejects_kind_and_producer_mismatch(tmp_path: Path) -> None:
    draft_path = _write(tmp_path / "draft.json", _draft("deployment_assessment"))
    context = _context("security_assessment")
    context["producer"]["agent"] = "deploy-navigator"
    context_path = _write(tmp_path / "context.json", context)
    output_path = tmp_path / "handoff.json"

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        output_path,
    )

    assert result.returncode == 4
    assert "does not match trusted context assessment_kind" in result.stderr
    assert "does not match trusted assessment_kind" in result.stderr
    assert not output_path.exists()


def test_stamp_enforces_trusted_risk_floor(tmp_path: Path) -> None:
    draft = _draft()
    draft["decision"]["risk_tier"] = "T0"
    context = _context()
    context["risk_tier_floor"] = "T2"
    draft_path = _write(tmp_path / "draft.json", draft)
    context_path = _write(tmp_path / "context.json", context)
    output_path = tmp_path / "handoff.json"

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        output_path,
    )

    assert result.returncode == 4
    assert "below trusted floor T2" in result.stderr
    assert not output_path.exists()


@pytest.mark.parametrize("kind", list(AGENTS))
def test_stamp_strips_only_allowlisted_foundry_null_placeholders(
    tmp_path: Path, kind: str
) -> None:
    draft = _draft(kind)
    if not draft["actions"]:
        release_action = _action("REL")
        release_action["blocking"] = False
        draft["actions"] = [release_action]
    evidence = draft["actions"][0]["evidence"][0]
    evidence["line_start"] = None
    evidence["line_end"] = None
    evidence["digest"] = None
    if kind == "deployment_assessment":
        draft["payload"]["rollback_reference"] = None
    elif kind == "release_assessment":
        draft["payload"]["revision"] = None
    elif kind == "security_assessment":
        draft["actions"][0]["cwe"] = None
    draft_path = _write(tmp_path / "draft.json", draft)
    context_path = _write(tmp_path / "context.json", _context(kind))
    output_path = tmp_path / "handoff.json"

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        output_path,
    )

    assert result.returncode == 0, result.stderr
    handoff = json.loads(output_path.read_text(encoding="utf-8"))
    emitted_evidence = handoff["actions"][0]["evidence"][0]
    assert "line_start" not in emitted_evidence
    assert "line_end" not in emitted_evidence
    assert "digest" not in emitted_evidence
    if kind == "deployment_assessment":
        assert "rollback_reference" not in handoff["payload"]
    elif kind == "release_assessment":
        assert "revision" not in handoff["payload"]
    elif kind == "security_assessment":
        assert "cwe" not in handoff["actions"][0]


def test_stamp_does_not_strip_arbitrary_nulls(tmp_path: Path) -> None:
    draft = _draft()
    draft["actions"][0]["title"] = None
    draft["actions"][0]["cwe"] = None
    draft_path = _write(tmp_path / "draft.json", draft)
    context_path = _write(tmp_path / "context.json", _context())
    output_path = tmp_path / "handoff.json"

    result = _run(
        "stamp",
        "--draft",
        draft_path,
        "--context",
        context_path,
        "--output",
        output_path,
    )

    assert result.returncode == 4
    assert "expected string" in result.stderr
    assert "additional property is not allowed" in result.stderr
    assert not output_path.exists()
