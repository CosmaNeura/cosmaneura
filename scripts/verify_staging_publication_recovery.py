"""Authenticate one explicit recovery of an already-pushed canonical candidate.

Only GitHub reads occur here. Original failed publication remains failed.
Fresh protected configuration, archive loading, registry equality and deployment
remain separate existing workflow gates. No registry credential is used here.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

REPOSITORY = "CosmaNeura/cosmaneura"
RUN = 41090712649
ATTEMPT = 1
CONTROL = "087fa450c8801742b8dd085f175337e462cea85c"
WORKFLOW_SHA256 = "a76aba4c5964dca2310f3527a96768f0489ff28af58233b76994e15671cbdf4a"
CANDIDATE = "24b75868a9a9c02ad035578c070f8a46c5589e43"
DIGEST = "sha256:4a3e720bdc212d80a65842ed6a3d7e87bda66b9aaa36b588963fee874b6fecfb"
ARCHIVE = "sha256:bcf935071a9acd67ca567b2a0393d7dd97f581d7bda4dbe6773ea89c1a45caf3"
IMAGE_ID = "sha256:4b40f8d87882876edc69221645d0451f68c135de019ae9cc8d2b26ca66809ac2"
RUNTIME_PUBLIC = {'AGENT_API_COMPANION_CLIENT_ID': '35db5d52-cfa6-4689-9031-3a1e09c572fc', 'AGENT_API_SCRIBE_RECEIVER_CLIENT_ID': 'ce52fcea-db46-4436-a4fd-e07c84703aae', 'COMPANION_APPOINTMENT_SETUP_ENABLED': 'true', 'COMPANION_ENCOUNTER_READ_ENABLED': 'true', 'COMPANION_RECORD_READ_ENABLED': 'true', 'SCRIBE_CAPTURE_ENABLED': 'true', 'SCRIBE_CAPTURE_LONG_VISITS_ENABLED': 'true', 'SCRIBE_CAPTURE_NOTES_ENABLED': 'true', 'SCRIBE_CAPTURE_RECOVERY_ENABLED': 'false', 'SCRIBE_CAPTURE_SOURCE_ENABLED': 'false', 'SCRIBE_CAPTURE_UPLOAD_ORIGINS': 'https://cosmaneurastaginguploads.blob.core.windows.net', 'SCRIBE_CAPTURE_WORKING_TRANSCRIPTS_ENABLED': 'false', 'SCRIBE_COMBINED_SESSION_PREPARATION_ENABLED': 'true', 'SCRIBE_ENCOUNTER_PREP_ENABLED': 'true', 'SCRIBE_MANAGED_IDENTITY_CLIENT_ID': 'aea02ca6-0239-435c-b54d-22ccfdc23380', 'SCRIBE_NATIVE_REQUEST_TIMING': 'true', 'SCRIBE_SERVICE_BASE_URL': 'https://cosmaneura-staging-scribe-api.internal.politesmoke-7989878f.northcentralus.azurecontainerapps.io', 'SCRIBE_SERVICE_SCOPE': 'api://57264fe1-6a4a-4946-8412-e0be18805977/.default', 'SCRIBE_SERVICE_TIMEOUT_SECONDS': '60', 'SCRIBE_WORKFLOW_ENABLED': 'true', 'SCRIBE_WORKFLOW_IDENTITY_REUSE_ENABLED': 'true', 'SESSION_PREP_HEALTH_ALERTS_ENABLED': 'true', 'SESSION_PREP_PROVIDER_ENABLED': 'true', 'ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED': 'true'}
ARTIFACTS = {
    "attempt": (12995278538, "7a2cc3dd6a947558de248f1ffbd4af700da5f8d40ccdc6de9f5d8ad862d1ef83"),
    "frontend": (12996255963, "f37498f5079488677a755252b5c04c8f9500204cfbae78c9a78d97f8eef5dd0e"),
    "publication": (12994994727, "dd63dc0cbf6728c163354b1a99e07b13e5f6259d1dd0d34d88548688c65e0017"),
    "image": (12995767266, "eb7257978b26ed6074fe65fdb069b1fcd448dcbdb2bc7374cb8a108b9091dff3"),
}


def require(value: object) -> None:
    if not value:
        raise ValueError("canonical_publication_recovery_refused")


def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def parse(raw: bytes | str) -> dict:
    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict)
    return value


def gh(path: str, *, binary: bool = False) -> bytes:
    command = ["gh", "api", "--hostname", "github.com"]
    if binary:
        command.append("--allow-escape-sequences")
    command.append(f"repos/{REPOSITORY}/{path}")
    result = subprocess.run(command, capture_output=True, timeout=60, check=False)
    require(result.returncode == 0 and 0 < len(result.stdout) <= 2_000_000)
    return result.stdout


def artifact(kind: str, *, download: bool = True) -> dict[str, bytes]:
    identifier, expected = ARTIFACTS[kind]
    metadata = parse(gh(f"actions/artifacts/{identifier}"))
    require(metadata["id"] == identifier and metadata["digest"] == "sha256:" + expected)
    require(metadata["expired"] is False and metadata["workflow_run"]["id"] == RUN
            and metadata["workflow_run"]["head_sha"] == CONTROL)
    if not download:
        return {}
    require(metadata["size_in_bytes"] < 10_000)
    raw = gh(f"actions/artifacts/{identifier}/zip", binary=True)
    require(hashlib.sha256(raw).hexdigest() == expected)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(len(archive.infolist()) <= 3)
        result = {}
        for entry in archive.infolist():
            require(entry.filename in {"attempt.json", "publication.json", "SHA256SUMS", "staging-frontend-build.json"}
                    and entry.filename not in result and entry.file_size < 20_000)
            result[entry.filename] = archive.read(entry)
    return result


def checked_json(files: dict[str, bytes], name: str) -> dict:
    require(set(files) == {name, "SHA256SUMS"})
    expected = hashlib.sha256(files[name]).hexdigest() + "  " + name + "\n"
    require(files["SHA256SUMS"].decode() == expected)
    return parse(files[name])


def validate(selection: dict, attempt: dict, publication: dict, frontend: bytes,
             run: dict, jobs: dict, original_workflow: bytes, log: str) -> dict:
    require(selection == {"run_id": RUN, "run_attempt": ATTEMPT, "image_digest": DIGEST})
    require(run["id"] == RUN and run["head_sha"] == CONTROL and run["run_attempt"] == ATTEMPT
            and run["status"] == "completed" and run["conclusion"] == "failure"
            and run["event"] == "workflow_dispatch" and run["head_branch"] == "master"
            and run["actor"]["login"] == run["triggering_actor"]["login"] == "JohnClem"
            and run["path"] == ".github/workflows/deploy_staging_candidate.yml")
    require(hashlib.sha256(original_workflow).hexdigest() == WORKFLOW_SHA256)
    require(attempt["workflow_control_sha"] == CONTROL and attempt["github"] == {
        "repository": REPOSITORY, "ref": "refs/heads/master", "ref_protected": True,
        "actor": "JohnClem", "triggering_actor": "JohnClem", "run_id": RUN, "run_attempt": ATTEMPT,
    })
    expected_requested = {
        "candidate_sha": os.environ["CANDIDATE_SHA"], "approval_pr_number": os.environ["APPROVAL_PR_NUMBER"],
        "synthetic_only_confirmed": os.environ["SYNTHETIC_ONLY_CONFIRMED"] == "true",
        "synthetic_evidence_sha256": os.environ["SYNTHETIC_EVIDENCE_SHA256"],
        "staging_identity_fingerprints_sha256": os.environ["STAGING_IDENTITY_FINGERPRINTS_SHA256"],
        "staging_effective_configuration_sha256": os.environ["STAGING_EFFECTIVE_CONFIGURATION_SHA256"],
        "recovery_digest": os.environ["RECOVERY_DIGEST"],
        "expected_schema_before": os.environ["EXPECTED_SCHEMA_BEFORE"],
        "expected_schema_after": os.environ["EXPECTED_SCHEMA_AFTER"],
    }
    require(expected_requested["candidate_sha"] == CANDIDATE and attempt["requested"] == expected_requested)
    expected_jobs = {"authorization": "success", "backend": "success", "frontend": "success",
                     "public_configuration": "success", "credentialless_image_build": "success",
                     "publication": "failure", "deployment": "skipped"}
    require(attempt["jobs"] == expected_jobs)
    evidence = attempt["available_evidence"]
    require(evidence["build_archive_sha256"] == ARCHIVE and evidence["build_image_id"] == IMAGE_ID
            and evidence["frontend_config_sha256"] == "sha256:" + hashlib.sha256(frontend).hexdigest()
            and evidence["observed_source_sha"] is None and evidence["image_digest"] is None)
    require(publication == {"recorded_at": publication["recorded_at"], "workflow_control_sha": CONTROL,
        "source_sha": CANDIDATE, "image": {"digest": None, "reference": None},
        "publication_outcome": "failure", "run_id": RUN, "run_attempt": ATTEMPT})
    rows = jobs["jobs"]
    require(jobs["total_count"] == len(rows) and len({row["name"] for row in rows}) == len(rows)
            and all(row["run_id"] == RUN and row["run_attempt"] == ATTEMPT for row in rows))
    observed = {row["name"]: row for row in rows}
    shard_names = re.findall(rb"          - shard: ([a-z0-9-]+)\n", original_workflow)
    require(len(shard_names) == 31 and len(set(shard_names)) == 31)
    expected_success = {"Authorize immutable staging candidate", "Candidate backend quality", "Candidate Frontend",
                        "Candidate Backend", "Resolve reviewed public staging build configuration",
                        "Build exact candidate without protected credentials"}
    expected_success.update(f"Candidate backend tests ({name.decode()})" for name in shard_names)
    expected_names = expected_success | {"Publish credential-free candidate archive",
        "Deploy exact candidate to persistent staging", "Record complete staging workflow attempt"}
    require(set(observed) == expected_names and all(observed[name]["conclusion"] == "success" for name in expected_success))
    publisher = observed["Publish credential-free candidate archive"]
    require(publisher["id"] == 124200892393 and publisher["conclusion"] == "failure"
            and observed["Deploy exact candidate to persistent staging"]["conclusion"] == "skipped")
    failed_steps = [step["name"] for step in publisher["steps"] if step["conclusion"] == "failure"]
    require(failed_steps == ["Publish candidate SHA tag and resolve immutable digest"])
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", log)
    digest_lines = re.findall(r"^\S+Z " + CANDIDATE + r": digest: (sha256:[0-9a-f]{64}) size: [0-9]+$", clean, re.M)
    require(digest_lines == [DIGEST] and "##[error]The published candidate did not resolve to an immutable digest." in clean
            and f"EXPECTED_ARCHIVE_SHA256: {ARCHIVE}" in clean and f"EXPECTED_IMAGE_ID: {IMAGE_ID}" in clean)
    return {"schema_version": 1, "action": "verify_original_failed_publication_for_read_only_adoption",
            "original_run_id": RUN, "original_run_attempt": ATTEMPT, "original_controller_sha": CONTROL,
            "candidate_sha": CANDIDATE, "image_digest": DIGEST, "archive_sha256": ARCHIVE,
            "image_id": IMAGE_ID, "frontend_config_sha256": evidence["frontend_config_sha256"],
            "original_jobs": expected_jobs, "original_publication_still_failed": True,
            "all_original_candidate_jobs_verified": True, "publication_log_sha256": hashlib.sha256(log.encode()).hexdigest(),
            "registry_mutations": 0, "deployment_operations": 0}


def main() -> None:
    selection = parse(os.environ["PUBLICATION_RECOVERY_JSON"])
    require(parse(os.environ["STAGING_RUNTIME_PUBLIC_JSON"]) == RUNTIME_PUBLIC)
    original_workflow = subprocess.check_output(["git", "show", CONTROL + ":.github/workflows/deploy_staging_candidate.yml"])
    attempt = checked_json(artifact("attempt"), "attempt.json")
    publication = checked_json(artifact("publication"), "publication.json")
    frontend_files = artifact("frontend")
    require(set(frontend_files) == {"staging-frontend-build.json"})
    artifact("image", download=False)
    result = validate(selection, attempt, publication, frontend_files["staging-frontend-build.json"],
        parse(gh(f"actions/runs/{RUN}")), parse(gh(f"actions/runs/{RUN}/attempts/{ATTEMPT}/jobs?per_page=100")),
        original_workflow, gh("actions/jobs/124200892393/logs", binary=True).decode("utf-8-sig"))
    target = Path("staging-publication-recovery-evidence")
    target.mkdir(mode=0o700)
    (target / "recovery.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    outputs = {"recovery_verified": "true", "recovery_run_id": str(RUN), "recovery_run_attempt": str(ATTEMPT),
        "recovery_image_digest": DIGEST, "recovery_archive_sha256": ARCHIVE, "recovery_image_id": IMAGE_ID,
        "recovery_frontend_config_sha256": result["frontend_config_sha256"],
        "recovery_backend_result": "success", "recovery_frontend_result": "success", "recovery_build_result": "success"}
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")
    print("Original failed publication authenticated; fresh registry/configuration/deployment gates still required.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, zipfile.BadZipFile):
        raise SystemExit("canonical_publication_recovery_refused") from None
