import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
BUILDER = ROOT / ".github/scripts/build_agent_evidence.py"
HANDOFFCTL = ROOT / ".github/scripts/handoffctl.py"

BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40
REPOSITORY = "CosmasCode/cosmaneura"
POLICY_SHA = "c" * 40


def _pr() -> dict[str, object]:
    return {
        "repository": REPOSITORY,
        "number": 123,
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "base_ref": "master",
        "state": "open",
        "draft": False,
    }


def _file(
    path: str = "app/configuration/service_auth.py",
    patch: str = "@@ -1 +1 @@\n-old = False\n+enabled = True",
) -> dict[str, object]:
    return {
        "path": path,
        "status": "modified",
        "additions": 1,
        "deletions": 1,
        "changes": 2,
        "patch": patch,
    }


def _changes(files: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "repository": REPOSITORY,
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "complete": True,
        "files": [_file()] if files is None else files,
    }


def _check(
    *,
    identifier: int = 901,
    name: str = "CodeQL",
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict[str, object]:
    return {
        "id": identifier,
        "name": name,
        "head_sha": HEAD_SHA,
        "status": status,
        "conclusion": conclusion,
        "started_at": "2026-08-25T12:00:00Z",
        "completed_at": ("2026-08-25T12:05:00Z" if status == "completed" else None),
    }


def _checks(check_runs: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "repository": REPOSITORY,
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "complete": True,
        "check_runs": [_check()] if check_runs is None else check_runs,
    }


def _policy() -> dict[str, object]:
    return {
        "policy_version": "cosmaneura.agent-trusted-policy.v1",
        "repository": REPOSITORY,
        "policy_sha": POLICY_SHA,
        "default_branch": "master",
        "deploy_workflow_path": ".github/workflows/build_and_publish.yml",
        "environment_branches": {
            "production": "master",
            "staging": "master",
        },
        "migration_playbook_path": "docs/MIGRATION_PLAYBOOK.md",
        "branch_protection_enforced": False,
        "production_reviewer_enforced": False,
    }


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _run(
    tmp_path: Path,
    *,
    pr: object | None = None,
    changes: object | None = None,
    checks: object | None = None,
    policy: object | None = None,
    output_name: str = "evidence.json",
    extra: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], Path]:
    pr_path = _write(tmp_path / "pr.json", _pr() if pr is None else pr)
    changes_path = _write(
        tmp_path / "changes.json", _changes() if changes is None else changes
    )
    checks_path = _write(
        tmp_path / "checks.json", _checks() if checks is None else checks
    )
    output = tmp_path / output_name
    policy_arguments: list[str] = []
    if policy is not None:
        policy_path = _write(tmp_path / "policy.json", policy)
        policy_arguments = ["--policy", str(policy_path)]
    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--pr",
            str(pr_path),
            "--changes",
            str(changes_path),
            "--checks",
            str(checks_path),
            "--output",
            str(output),
            *policy_arguments,
            *extra,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return result, output


def test_builds_deterministic_exact_sha_sanitized_evidence(tmp_path: Path) -> None:
    result, output = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    pack = json.loads(output.read_text(encoding="utf-8"))
    assert pack["schema_version"] == "cosmaneura.agent-evidence-pack.v1"
    assert pack["subject"] == {
        "repository": REPOSITORY,
        "pull_request": 123,
        "base_ref": "master",
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
    }
    assert pack["risk_tier_floor"] == "T2"
    assert all(
        item["trust"] == "untrusted"
        for item in pack["evidence"]
        if item["type"] in {"pull_request_metadata", "code_diff", "check_run"}
    )
    assert pack["evidence_policy"]["semantic_source"]["status"] == "unavailable"
    assert pack["evidence_policy"]["migration_rehearsal"]["status"] == ("unavailable")
    assert (
        "app/configuration/service_auth.py"
        in pack["evidence_policy"]["allowed_action_evidence_locators"]
    )
    manifest = pack["evidence_policy"]["evidence_manifest"]
    allowed = pack["evidence_policy"]["allowed_action_evidence_locators"]
    assert allowed == sorted(item["locator"] for item in manifest)
    assert {item["id"] for item in pack["evidence"]} <= set(allowed)
    assert pack["evidence_policy"]["pull_request"] == {
        "locator": "pull-request:123",
        "state": "open",
        "draft": False,
    }
    assert pack["evidence_policy"]["checks"] == [
        {
            "locator": "check-run:901",
            "check_run_id": 901,
            "name": "CodeQL",
            "required": False,
            "status": "completed",
            "conclusion": "success",
        }
    ]
    check_manifest = next(
        item for item in manifest if item["locator"] == "check-run:901"
    )
    assert check_manifest["required_for_deployment"] is False
    unavailable_policy = next(
        item for item in manifest if item["locator"] == "repository-policy:unavailable"
    )
    assert unavailable_policy == {
        "locator": "repository-policy:unavailable",
        "availability": "unavailable",
        "required_for_deployment": True,
    }
    diff_evidence = next(
        item for item in pack["evidence"] if item["type"] == "code_diff"
    )
    assert "patch" not in diff_evidence
    assert diff_evidence["diff"] == {
        "format": "unified-diff-structure.v1",
        "content_redacted": True,
        "content_digest": (
            "sha256:" + hashlib.sha256(_file()["patch"].encode("utf-8")).hexdigest()
        ),
        "hunks": [
            {
                "old_start": 1,
                "old_lines": 1,
                "new_start": 1,
                "new_lines": 1,
                "added_lines": 1,
                "deleted_lines": 1,
                "context_lines": 0,
            }
        ],
    }
    assert diff_evidence["destructive_migration_signal"] is False
    check_evidence = next(
        item for item in pack["evidence"] if item["type"] == "check_run"
    )
    assert check_evidence["id"] == "check-run:901"
    assert check_evidence["check_run_id"] == 901
    emitted_evidence = json.dumps(pack["evidence"])
    assert not any(
        forbidden in emitted_evidence
        for forbidden in (
            "body",
            "author",
            "details_url",
            "raw_url",
            "logs",
            "old = False",
            "enabled = True",
        )
    )

    scanner_status = {
        item["scanner"]: item["status"] for item in pack["scanner_coverage"]
    }
    assert scanner_status == {
        "CodeQL/SAST": "unavailable",
        "dependency": "not_configured",
        "secret": "not_configured",
        "IaC": "not_configured",
        "SBOM": "not_configured",
        "container": "not_configured",
    }
    scanner_evidence = [
        item for item in pack["evidence"] if item["type"] == "scanner_coverage"
    ]
    assert len(scanner_evidence) == 6
    assert {item["scanner"] for item in scanner_evidence} == set(scanner_status)

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", pack["integrity"]["input_digest"])


def test_same_normalized_inputs_produce_identical_output(tmp_path: Path) -> None:
    first_result, first_output = _run(tmp_path, output_name="first.json")
    first_bytes = first_output.read_bytes()
    second_result, second_output = _run(tmp_path, output_name="second.json")

    assert first_result.returncode == second_result.returncode == 0
    assert second_output.read_bytes() == first_bytes


def test_trusted_policy_is_bound_emitted_once_and_included_in_digest(
    tmp_path: Path,
) -> None:
    policy = _policy()

    result, output = _run(tmp_path, policy=policy)

    assert result.returncode == 0, result.stderr
    pack = json.loads(output.read_text(encoding="utf-8"))
    policy_evidence = [
        item for item in pack["evidence"] if item["type"] == "repository_policy"
    ]
    assert policy_evidence == [
        {
            "id": f"repository-policy:{POLICY_SHA}",
            "type": "repository_policy",
            "trust": "trusted",
            **policy,
        }
    ]
    manifest_policy = next(
        item
        for item in pack["evidence_policy"]["evidence_manifest"]
        if item["locator"] == f"repository-policy:{POLICY_SHA}"
    )
    assert manifest_policy["availability"] == "available"
    assert manifest_policy["required_for_deployment"] is True
    assert (
        ".github/workflows/build_and_publish.yml"
        in pack["evidence_policy"]["allowed_action_evidence_locators"]
    )
    assert (
        "docs/MIGRATION_PLAYBOOK.md"
        in pack["evidence_policy"]["allowed_action_evidence_locators"]
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", pack["integrity"]["input_digest"])


def test_emitted_evidence_policy_is_valid_broker_context_input(tmp_path: Path) -> None:
    result, output = _run(tmp_path, policy=_policy())
    assert result.returncode == 0, result.stderr
    pack = json.loads(output.read_text(encoding="utf-8"))
    context = {
        "context_version": "cosmaneura.agent-broker-context.v1",
        "assessment_kind": "deployment_assessment",
        "producer": {
            "agent": "deploy-navigator",
            "policy_version": "1.0.0",
            "foundry_agent_version": "4",
            "model": "gpt-5.6-sol",
        },
        "policy_sha": POLICY_SHA,
        "subject": {
            "repository": REPOSITORY,
            "pull_request": 123,
            "base_sha": BASE_SHA,
            "head_sha": HEAD_SHA,
            "environment": "production",
        },
        "risk_tier_floor": pack["risk_tier_floor"],
        "generated_at": "2026-08-25T12:00:00Z",
        "evidence_fresh_until": "2026-08-25T14:00:00Z",
        "evidence_policy": pack["evidence_policy"],
        "input_digest": pack["integrity"]["input_digest"],
    }
    context_path = _write(tmp_path / "context.json", context)

    validation = subprocess.run(
        [
            sys.executable,
            str(HANDOFFCTL),
            "validate",
            str(context_path),
            "--contract",
            "context",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert validation.returncode == 0, validation.stderr


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("repository", "Other/repository", "does not match PR repository"),
        ("policy_sha", "abc123", "complete lowercase 40- or 64-hex SHA"),
        ("default_branch", "develop", "does not match configured default branch"),
    ],
)
def test_rejects_unbound_trusted_policy(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    policy = _policy()
    policy[field] = value

    result, output = _run(tmp_path, policy=policy)

    assert result.returncode == 4
    assert expected in result.stderr
    assert not output.exists()


def test_rejects_policy_fields_outside_small_allowlist(tmp_path: Path) -> None:
    policy = _policy()
    policy["production_subscription_id"] = "not-allowed"

    result, output = _run(tmp_path, policy=policy)

    assert result.returncode == 4
    assert "1 unexpected field(s) are not allowed" in result.stderr
    assert "production_subscription_id" not in result.stderr
    assert not output.exists()


def test_policy_facts_change_input_digest(tmp_path: Path) -> None:
    first_result, first_output = _run(
        tmp_path, policy=_policy(), output_name="first.json"
    )
    first_digest = json.loads(first_output.read_text())["integrity"]["input_digest"]
    changed_policy = _policy()
    changed_policy["branch_protection_enforced"] = True
    second_result, second_output = _run(
        tmp_path, policy=changed_policy, output_name="second.json"
    )
    second_digest = json.loads(second_output.read_text())["integrity"]["input_digest"]

    assert first_result.returncode == second_result.returncode == 0
    assert first_digest != second_digest


@pytest.mark.parametrize("source", ["changes", "checks", "check_run"])
def test_rejects_any_sha_mismatch(tmp_path: Path, source: str) -> None:
    changes = _changes()
    checks = _checks()
    if source == "changes":
        changes["head_sha"] = "c" * 40
    elif source == "checks":
        checks["base_sha"] = "c" * 40
    else:
        checks["check_runs"][0]["head_sha"] = "c" * 40

    result, output = _run(tmp_path, changes=changes, checks=checks)

    assert result.returncode == 4
    assert "does not match PR" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "secrets/production.json",
        "config/client.key",
        "tests/data/recording.wav",
        "var/application.log",
    ],
)
def test_rejects_sensitive_or_binary_paths(tmp_path: Path, path: str) -> None:
    result, output = _run(tmp_path, changes=_changes([_file(path=path)]))

    assert result.returncode == 4
    assert "forbidden sensitive or binary path" in result.stderr
    assert not output.exists()


def test_rejects_missing_or_oversize_patch(tmp_path: Path) -> None:
    missing_patch = _file()
    missing_patch["patch"] = None
    result, _ = _run(tmp_path, changes=_changes([missing_patch]))
    assert result.returncode == 4

    oversize_patch = "@@ -1 +1 @@\n+" + ("x" * (64 * 1024))
    result, output = _run(
        tmp_path,
        changes=_changes([_file(patch=oversize_patch)]),
        output_name="oversize.json",
    )
    assert result.returncode == 4
    assert "exceeds 65536 UTF-8 bytes" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    ("literal", "message"),
    [
        ("ghp_abcdefghijklmnopqrstuvwxyz1234", "possible GitHub token"),
        ("patient@example.com", "possible PHI"),
        ("123-45-6789", "possible PHI"),
    ],
)
def test_rejects_secret_like_and_phi_literals(
    tmp_path: Path, literal: str, message: str
) -> None:
    patch = f"@@ -1 +1 @@\n-old\n+{literal}"

    result, output = _run(tmp_path, changes=_changes([_file(patch=patch)]))

    assert result.returncode == 4
    assert message in result.stderr
    assert literal not in result.stderr
    assert not output.exists()


def test_rejects_raw_check_output(tmp_path: Path) -> None:
    check = _check()
    check["output"] = {"text": "raw workflow output"}

    result, output = _run(tmp_path, checks=_checks([check]))

    assert result.returncode == 4
    assert "raw log/output field(s) forbidden: output" in result.stderr
    assert not output.exists()


def test_rejects_incomplete_changed_file_collection(tmp_path: Path) -> None:
    changes = _changes()
    changes["complete"] = False

    result, output = _run(tmp_path, changes=changes)

    assert result.returncode == 4
    assert "all changed-file pages are required" in result.stderr
    assert not output.exists()


def test_incomplete_check_collection_marks_missing_scanners_unavailable(
    tmp_path: Path,
) -> None:
    checks = _checks([])
    checks["complete"] = False

    result, output = _run(tmp_path, checks=checks)

    assert result.returncode == 0, result.stderr
    pack = json.loads(output.read_text(encoding="utf-8"))
    assert {item["status"] for item in pack["scanner_coverage"]} == {"unavailable"}
    assert all(
        item["evidence_locators"] == ["check-run-collection:incomplete"]
        for item in pack["scanner_coverage"]
    )


def test_skipped_scanner_is_explicitly_unavailable(tmp_path: Path) -> None:
    check = _check(name="CodeQL", conclusion="skipped")

    result, output = _run(tmp_path, checks=_checks([check]))

    assert result.returncode == 0, result.stderr
    pack = json.loads(output.read_text(encoding="utf-8"))
    codeql = next(
        item for item in pack["scanner_coverage"] if item["scanner"] == "CodeQL/SAST"
    )
    assert codeql == {
        "scanner": "CodeQL/SAST",
        "status": "unavailable",
        "evidence_locators": ["check-run:901"],
    }


def test_successful_check_name_cannot_claim_scanner_availability(
    tmp_path: Path,
) -> None:
    check = _check(name="CodeQL / SAST / definitely trusted", conclusion="success")

    result, output = _run(tmp_path, checks=_checks([check]))

    assert result.returncode == 0, result.stderr
    pack = json.loads(output.read_text(encoding="utf-8"))
    codeql = next(
        item for item in pack["scanner_coverage"] if item["scanner"] == "CodeQL/SAST"
    )
    assert codeql["status"] == "unavailable"
    assert codeql["evidence_locators"] == ["check-run:901"]
    assert (
        "never establish scanner availability"
        in pack["sanitization"]["scanner_provenance"]
    )


@pytest.mark.parametrize(
    ("path", "patch", "expected"),
    [
        ("docs/release-notes.md", "@@ -1 +1 @@\n-old\n+new", "T0"),
        ("app/routes/status.py", "@@ -1 +1 @@\n-old\n+new", "T1"),
        (
            "migrations/versions/remove_field.py",
            "@@ -1 +1 @@\n-old\n+op.drop_column('table', 'field')",
            "T2",
        ),
    ],
)
def test_derives_conservative_path_risk_floor(
    tmp_path: Path, path: str, patch: str, expected: str
) -> None:
    result, output = _run(tmp_path, changes=_changes([_file(path=path, patch=patch)]))

    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["risk_tier_floor"] == expected


def test_rejects_non_default_base_branch(tmp_path: Path) -> None:
    pr = _pr()
    pr["base_ref"] = "feature"

    result, output = _run(tmp_path, pr=pr)

    assert result.returncode == 4
    assert "expected configured default branch 'master'" in result.stderr
    assert not output.exists()


def test_rejects_binary_json_input(tmp_path: Path) -> None:
    pr_path = tmp_path / "pr.json"
    pr_path.write_bytes(b'{"repository":"owner/repo"}\x00')
    changes_path = _write(tmp_path / "changes.json", _changes())
    checks_path = _write(tmp_path / "checks.json", _checks())
    output = tmp_path / "evidence.json"

    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--pr",
            str(pr_path),
            "--changes",
            str(changes_path),
            "--checks",
            str(checks_path),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 4
    assert "binary/NUL input is forbidden" in result.stderr
    assert not output.exists()


def test_rejects_oversize_json_input_before_parsing(tmp_path: Path) -> None:
    pr_path = tmp_path / "pr.json"
    pr_path.write_bytes(b" " * (128 * 1024 + 1))
    changes_path = _write(tmp_path / "changes.json", _changes())
    checks_path = _write(tmp_path / "checks.json", _checks())
    output = tmp_path / "evidence.json"

    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--pr",
            str(pr_path),
            "--changes",
            str(changes_path),
            "--checks",
            str(checks_path),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 4
    assert "input exceeds 131072 bytes" in result.stderr
    assert not output.exists()


def test_patch_content_is_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    patch = (
        "@@ -1 +1 @@\n-old\n+__import__('pathlib').Path('" + str(marker) + "').touch()"
    )

    result, output = _run(tmp_path, changes=_changes([_file(patch=patch)]))

    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert not marker.exists()
    assert str(marker) not in output.read_text(encoding="utf-8")


def test_diff_source_lines_comments_and_literals_are_never_emitted(
    tmp_path: Path,
) -> None:
    source_literal = "internal_customer_label_omega_8472"
    patch = (
        "@@ -4,2 +4,3 @@ private_context_label\n"
        "-old_identifier = 17\n"
        "+new_identifier = 23\n"
        f"+# comment {source_literal}\n"
        " unchanged_call()"
    )
    changed = _file(patch=patch)
    changed["additions"] = 2
    changed["deletions"] = 1
    changed["changes"] = 3

    result, output = _run(tmp_path, changes=_changes([changed]))

    assert result.returncode == 0, result.stderr
    emitted = output.read_text(encoding="utf-8")
    for forbidden in (
        source_literal,
        "private_context_label",
        "old_identifier",
        "new_identifier",
        "unchanged_call",
    ):
        assert forbidden not in emitted


def test_redacted_diff_digest_still_binds_exact_source_content(tmp_path: Path) -> None:
    first_patch = "@@ -1 +1 @@\n-old_value\n+first_value"
    second_patch = "@@ -1 +1 @@\n-old_value\n+second_value"

    first_result, first_output = _run(
        tmp_path,
        changes=_changes([_file(patch=first_patch)]),
        output_name="first-content.json",
    )
    second_result, second_output = _run(
        tmp_path,
        changes=_changes([_file(patch=second_patch)]),
        output_name="second-content.json",
    )

    assert first_result.returncode == second_result.returncode == 0
    first = json.loads(first_output.read_text(encoding="utf-8"))
    second = json.loads(second_output.read_text(encoding="utf-8"))
    assert first["integrity"]["input_digest"] != second["integrity"]["input_digest"]
    first_diff = next(item for item in first["evidence"] if item["type"] == "code_diff")
    second_diff = next(
        item for item in second["evidence"] if item["type"] == "code_diff"
    )
    assert first_diff["diff"]["content_digest"] != second_diff["diff"]["content_digest"]
    assert "first_value" not in first_output.read_text(encoding="utf-8")
    assert "second_value" not in second_output.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (
            "@@ -1,2 +1 @@\n-old\n+new",
            "old-line count does not match the diff hunk header",
        ),
        (
            "@@ -1 +1 @@\n-old\nunsupported\n+new",
            "unsupported or malformed unified-diff line",
        ),
        (
            "diff --git a/file b/file\n@@ -1 +1 @@\n-old\n+new",
            "unsupported or malformed unified-diff structure",
        ),
    ],
)
def test_rejects_uncertain_or_unsupported_patch_forms(
    tmp_path: Path, patch: str, message: str
) -> None:
    result, output = _run(tmp_path, changes=_changes([_file(patch=patch)]))

    assert result.returncode == 4
    assert message in result.stderr
    assert not output.exists()
