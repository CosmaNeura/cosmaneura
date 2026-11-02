"""Authenticate exact allowlisted publication custody for canonical recovery.

Only GitHub reads occur here. Original publication and deployment outcomes remain unchanged.
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
RUNTIME_PUBLIC = {
    "AGENT_API_COMPANION_CLIENT_ID": "35db5d52-cfa6-4689-9031-3a1e09c572fc",
    "AGENT_API_SCRIBE_RECEIVER_CLIENT_ID": "ce52fcea-db46-4436-a4fd-e07c84703aae",
    "COMPANION_APPOINTMENT_SETUP_ENABLED": "true",
    "COMPANION_ENCOUNTER_READ_ENABLED": "true",
    "COMPANION_RECORD_READ_ENABLED": "true",
    "SCRIBE_CAPTURE_ENABLED": "true",
    "SCRIBE_CAPTURE_LONG_VISITS_ENABLED": "true",
    "SCRIBE_CAPTURE_NOTES_ENABLED": "true",
    "SCRIBE_CAPTURE_RECOVERY_ENABLED": "false",
    "SCRIBE_CAPTURE_SOURCE_ENABLED": "false",
    "SCRIBE_CAPTURE_UPLOAD_ORIGINS": "https://cosmaneurastaginguploads.blob.core.windows.net",
    "SCRIBE_CAPTURE_WORKING_TRANSCRIPTS_ENABLED": "false",
    "SCRIBE_COMBINED_SESSION_PREPARATION_ENABLED": "true",
    "SCRIBE_ENCOUNTER_PREP_ENABLED": "true",
    "SCRIBE_MANAGED_IDENTITY_CLIENT_ID": "aea02ca6-0239-435c-b54d-22ccfdc23380",
    "SCRIBE_NATIVE_REQUEST_TIMING": "true",
    "SCRIBE_SERVICE_BASE_URL": "https://cosmaneura-staging-scribe-api.internal.politesmoke-7989878f.northcentralus.azurecontainerapps.io",
    "SCRIBE_SERVICE_SCOPE": "api://57264fe1-6a4a-4946-8412-e0be18805977/.default",
    "SCRIBE_SERVICE_TIMEOUT_SECONDS": "60",
    "SCRIBE_WORKFLOW_ENABLED": "true",
    "SCRIBE_WORKFLOW_IDENTITY_REUSE_ENABLED": "true",
    "SESSION_PREP_HEALTH_ALERTS_ENABLED": "true",
    "SESSION_PREP_PROVIDER_ENABLED": "true",
    "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED": "true",
}
ARTIFACTS = {
    "attempt": (
        12995278538,
        "7a2cc3dd6a947558de248f1ffbd4af700da5f8d40ccdc6de9f5d8ad862d1ef83",
    ),
    "frontend": (
        12996255963,
        "f37498f5079488677a755252b5c04c8f9500204cfbae78c9a78d97f8eef5dd0e",
    ),
    "publication": (
        12994994727,
        "dd63dc0cbf6728c163354b1a99e07b13e5f6259d1dd0d34d88548688c65e0017",
    ),
    "image": (
        12995767266,
        "eb7257978b26ed6074fe65fdb069b1fcd448dcbdb2bc7374cb8a108b9091dff3",
    ),
}


# One additional exact reader profile; the original recovery remains unchanged.
READER039: dict = {
    "run": 41339202271,
    "attempt": 1,
    "control": "f6a8bd753c93fdf94c45d25b2649679570544a24",
    "workflow_sha256": "03106a057ce960fb47c7491995ad347fff138928804e942e33950356449bbc34",
    "candidate": "039c69c8d20bd07a9c6c0c8a7860ff411646fb18",
    "digest": "sha256:d8aa52214bcef19aed8988ca461540ff5f6bffbb1a4177e6816fdce583651ce1",
    "archive": "sha256:01b7266562ed9d7dae072a6042646c9c0a8dbd13c825f55975d9d62bcba0e3d8",
    "image_id": "sha256:80801ae254b75b92e4e0de4078e296299636ed922c97eaeb316a2ba28ab76592",
    "frontend_sha256": "sha256:2262aefddf2da8edc5c611f57aaae057d493d3e385093716c44aabd88cd03484",
    "requested": {
        "candidate_sha": "039c69c8d20bd07a9c6c0c8a7860ff411646fb18",
        "approval_pr_number": "934",
        "synthetic_only_confirmed": True,
        "synthetic_evidence_sha256": "sha256:b124e979943359f6425bd876252337fc4389d90b2ef8cd9ab1c941c3805cd98a",
        "staging_identity_fingerprints_sha256": "sha256:89a01b7cd392ccdfe15111093520f6dd220ffa6e5347c72b48992865dc369fcb",
        "staging_effective_configuration_sha256": "sha256:1b608bc734c0713cc6f92e62e86f17fd10bafc691dc3d13a13ce7ddf656244e8",
        "recovery_digest": "sha256:ab792e9bfccdd2e45af983959505a6fa12c1c5846412df0a0193cf303e31c857",
        "expected_schema_before": "3d40e4e90e05",
        "expected_schema_after": "312b217ebba4",
    },
    "artifacts": {
        "attempt": {
            "id": 13097906691,
            "digest": "sha256:bf4615b806ced84ef030a39a892e2df1e38649180c1457750e7dc771cf0d7543",
            "name": "canonical-staging-attempt-41339202271-1",
        },
        "publication": {
            "id": 13098460690,
            "digest": "sha256:c98770388c630b6b3d0db583ec525a87153a7b68dd4d1c8fad8452e0d5227c8e",
            "name": "canonical-staging-publication-039c69c8d20bd07a9c6c0c8a7860ff411646fb18-41339202271-1",
        },
        "frontend": {
            "id": 13096594916,
            "digest": "sha256:920953791a5ea727be482937a9b689af62791dba5d26f06550da7400de616b09",
            "name": "canonical-staging-frontend-config-41339202271-1",
        },
        "image": {
            "id": 13097364215,
            "digest": "sha256:ae63d0e7fbc7a286bfc313b25376e82fe4ddefad59bd7ca7a380fff97b008c40",
            "name": "canonical-staging-image-039c69c8d20bd07a9c6c0c8a7860ff411646fb18-41339202271-1",
        },
        "release": {
            "id": 13098426212,
            "digest": "sha256:abdf234900a0abec7a31230b092779a516ee2be2c0fcbdf335027472cd97ab18",
            "name": "canonical-staging-release-039c69c8d20bd07a9c6c0c8a7860ff411646fb18-41339202271-1",
        },
    },
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
    require(
        metadata["expired"] is False
        and metadata["workflow_run"]["id"] == RUN
        and metadata["workflow_run"]["head_sha"] == CONTROL
    )
    if not download:
        return {}
    require(metadata["size_in_bytes"] < 10_000)
    raw = gh(f"actions/artifacts/{identifier}/zip", binary=True)
    require(hashlib.sha256(raw).hexdigest() == expected)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(len(archive.infolist()) <= 3)
        result = {}
        for entry in archive.infolist():
            require(
                entry.filename
                in {
                    "attempt.json",
                    "publication.json",
                    "SHA256SUMS",
                    "staging-frontend-build.json",
                }
                and entry.filename not in result
                and entry.file_size < 20_000
            )
            result[entry.filename] = archive.read(entry)
    return result


def checked_json(files: dict[str, bytes], name: str) -> dict:
    require(set(files) == {name, "SHA256SUMS"})
    expected = hashlib.sha256(files[name]).hexdigest() + "  " + name + "\n"
    require(files["SHA256SUMS"].decode() == expected)
    return parse(files[name])


def validate(
    selection: dict,
    attempt: dict,
    publication: dict,
    frontend: bytes,
    run: dict,
    jobs: dict,
    original_workflow: bytes,
    log: str,
) -> dict:
    require(
        selection == {"run_id": RUN, "run_attempt": ATTEMPT, "image_digest": DIGEST}
    )
    require(
        run["id"] == RUN
        and run["head_sha"] == CONTROL
        and run["run_attempt"] == ATTEMPT
        and run["status"] == "completed"
        and run["conclusion"] == "failure"
        and run["event"] == "workflow_dispatch"
        and run["head_branch"] == "master"
        and run["actor"]["login"] == run["triggering_actor"]["login"] == "JohnClem"
        and run["path"] == ".github/workflows/deploy_staging_candidate.yml"
    )
    require(hashlib.sha256(original_workflow).hexdigest() == WORKFLOW_SHA256)
    require(
        attempt["workflow_control_sha"] == CONTROL
        and attempt["github"]
        == {
            "repository": REPOSITORY,
            "ref": "refs/heads/master",
            "ref_protected": True,
            "actor": "JohnClem",
            "triggering_actor": "JohnClem",
            "run_id": RUN,
            "run_attempt": ATTEMPT,
        }
    )
    expected_requested = {
        "candidate_sha": os.environ["CANDIDATE_SHA"],
        "approval_pr_number": os.environ["APPROVAL_PR_NUMBER"],
        "synthetic_only_confirmed": os.environ["SYNTHETIC_ONLY_CONFIRMED"] == "true",
        "synthetic_evidence_sha256": os.environ["SYNTHETIC_EVIDENCE_SHA256"],
        "staging_identity_fingerprints_sha256": os.environ[
            "STAGING_IDENTITY_FINGERPRINTS_SHA256"
        ],
        "staging_effective_configuration_sha256": os.environ[
            "STAGING_EFFECTIVE_CONFIGURATION_SHA256"
        ],
        "recovery_digest": os.environ["RECOVERY_DIGEST"],
        "expected_schema_before": os.environ["EXPECTED_SCHEMA_BEFORE"],
        "expected_schema_after": os.environ["EXPECTED_SCHEMA_AFTER"],
    }
    require(
        expected_requested["candidate_sha"] == CANDIDATE
        and attempt["requested"] == expected_requested
    )
    expected_jobs = {
        "authorization": "success",
        "backend": "success",
        "frontend": "success",
        "public_configuration": "success",
        "credentialless_image_build": "success",
        "publication": "failure",
        "deployment": "skipped",
    }
    require(attempt["jobs"] == expected_jobs)
    evidence = attempt["available_evidence"]
    require(
        evidence["build_archive_sha256"] == ARCHIVE
        and evidence["build_image_id"] == IMAGE_ID
        and evidence["frontend_config_sha256"]
        == "sha256:" + hashlib.sha256(frontend).hexdigest()
        and evidence["observed_source_sha"] is None
        and evidence["image_digest"] is None
    )
    require(
        publication
        == {
            "recorded_at": publication["recorded_at"],
            "workflow_control_sha": CONTROL,
            "source_sha": CANDIDATE,
            "image": {"digest": None, "reference": None},
            "publication_outcome": "failure",
            "run_id": RUN,
            "run_attempt": ATTEMPT,
        }
    )
    rows = jobs["jobs"]
    require(
        jobs["total_count"] == len(rows)
        and len({row["name"] for row in rows}) == len(rows)
        and all(row["run_id"] == RUN and row["run_attempt"] == ATTEMPT for row in rows)
    )
    observed = {row["name"]: row for row in rows}
    shard_names = re.findall(rb"          - shard: ([a-z0-9-]+)\n", original_workflow)
    require(len(shard_names) == 31 and len(set(shard_names)) == 31)
    expected_success = {
        "Authorize immutable staging candidate",
        "Candidate backend quality",
        "Candidate Frontend",
        "Candidate Backend",
        "Resolve reviewed public staging build configuration",
        "Build exact candidate without protected credentials",
    }
    expected_success.update(
        f"Candidate backend tests ({name.decode()})" for name in shard_names
    )
    expected_names = expected_success | {
        "Publish credential-free candidate archive",
        "Deploy exact candidate to persistent staging",
        "Record complete staging workflow attempt",
    }
    require(
        set(observed) == expected_names
        and all(observed[name]["conclusion"] == "success" for name in expected_success)
    )
    publisher = observed["Publish credential-free candidate archive"]
    require(
        publisher["id"] == 124200892393
        and publisher["conclusion"] == "failure"
        and observed["Deploy exact candidate to persistent staging"]["conclusion"]
        == "skipped"
    )
    failed_steps = [
        step["name"] for step in publisher["steps"] if step["conclusion"] == "failure"
    ]
    require(failed_steps == ["Publish candidate SHA tag and resolve immutable digest"])
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", log)
    digest_lines = re.findall(
        r"^\S+Z " + CANDIDATE + r": digest: (sha256:[0-9a-f]{64}) size: [0-9]+$",
        clean,
        re.M,
    )
    require(
        digest_lines == [DIGEST]
        and "##[error]The published candidate did not resolve to an immutable digest."
        in clean
        and f"EXPECTED_ARCHIVE_SHA256: {ARCHIVE}" in clean
        and f"EXPECTED_IMAGE_ID: {IMAGE_ID}" in clean
    )
    return {
        "schema_version": 1,
        "action": "verify_original_failed_publication_for_read_only_adoption",
        "original_run_id": RUN,
        "original_run_attempt": ATTEMPT,
        "original_controller_sha": CONTROL,
        "candidate_sha": CANDIDATE,
        "image_digest": DIGEST,
        "archive_sha256": ARCHIVE,
        "image_id": IMAGE_ID,
        "frontend_config_sha256": evidence["frontend_config_sha256"],
        "original_jobs": expected_jobs,
        "original_publication_still_failed": True,
        "all_original_candidate_jobs_verified": True,
        "publication_log_sha256": hashlib.sha256(log.encode()).hexdigest(),
        "registry_mutations": 0,
        "deployment_operations": 0,
    }


def reader039_artifact(kind: str, *, download: bool = True) -> dict[str, bytes]:
    spec = READER039["artifacts"][kind]
    metadata = parse(gh(f"actions/artifacts/{spec['id']}"))
    require(metadata["id"] == spec["id"] and metadata["digest"] == spec["digest"])
    require(metadata["name"] == spec["name"] and metadata["expired"] is False)
    custody = metadata["workflow_run"]
    require(
        custody["id"] == READER039["run"]
        and custody["head_sha"] == READER039["control"]
        and custody["repository_id"] == custody["head_repository_id"] == 881573489
        and custody["head_branch"] == "master"
    )
    if not download:
        require(kind == "image")
        return {}
    require(kind != "image" and metadata["size_in_bytes"] < 20_000)
    raw = gh(f"actions/artifacts/{spec['id']}/zip", binary=True)
    require("sha256:" + hashlib.sha256(raw).hexdigest() == spec["digest"])
    allowed = {
        "attempt": {"attempt.json", "SHA256SUMS"},
        "publication": {"publication.json", "SHA256SUMS"},
        "frontend": {"staging-frontend-build.json"},
        "release": {
            "request.json",
            "release.json",
            "azure-identity.json",
            "workflow-result.json",
            "SHA256SUMS",
        },
    }[kind]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(len(archive.infolist()) == len(allowed))
        files = {}
        for entry in archive.infolist():
            require(entry.filename in allowed and entry.filename not in files)
            require(entry.file_size < 20_000 and not entry.is_dir())
            require((entry.external_attr >> 16) & 0o170000 != 0o120000)
            files[entry.filename] = archive.read(entry)
    require(set(files) == allowed)
    if "SHA256SUMS" in files:
        lines = files["SHA256SUMS"].decode().splitlines()
        require(len(lines) == len(files) - 1 and len(set(lines)) == len(lines))
        require(
            set(lines)
            == {
                hashlib.sha256(value).hexdigest() + "  " + name
                for name, value in files.items()
                if name != "SHA256SUMS"
            }
        )
    return files


def validate_reader039(
    selection: dict,
    attempt: dict,
    publication: dict,
    request: dict,
    release: dict,
    frontend: bytes,
    run: dict,
    jobs: dict,
    original_workflow: bytes,
) -> dict:
    p = READER039
    require(
        type(selection.get("run_id")) is int
        and type(selection.get("run_attempt")) is int
    )
    require(type(selection.get("image_digest")) is str)
    require(
        selection
        == {
            "run_id": p["run"],
            "run_attempt": p["attempt"],
            "image_digest": p["digest"],
        }
    )
    require(hashlib.sha256(original_workflow).hexdigest() == p["workflow_sha256"])
    require(
        run["id"] == p["run"]
        and run["head_sha"] == p["control"]
        and run["run_attempt"] == p["attempt"]
        and run["status"] == "completed"
        and run["conclusion"] == "failure"
        and run["event"] == "workflow_dispatch"
        and run["head_branch"] == "master"
        and run["actor"]["login"] == run["triggering_actor"]["login"] == "JohnClem"
        and run["path"] == ".github/workflows/deploy_staging_candidate.yml"
    )
    github = {
        "repository": REPOSITORY,
        "ref": "refs/heads/master",
        "ref_protected": True,
        "actor": "JohnClem",
        "triggering_actor": "JohnClem",
        "run_id": p["run"],
        "run_attempt": p["attempt"],
    }
    require(
        attempt["workflow_control_sha"] == p["control"] and attempt["github"] == github
    )
    requested = p["requested"]
    require(attempt["requested"] == requested)
    environment = {
        "candidate_sha": "CANDIDATE_SHA",
        "approval_pr_number": "APPROVAL_PR_NUMBER",
        "synthetic_evidence_sha256": "SYNTHETIC_EVIDENCE_SHA256",
        "staging_identity_fingerprints_sha256": "STAGING_IDENTITY_FINGERPRINTS_SHA256",
        "staging_effective_configuration_sha256": "STAGING_EFFECTIVE_CONFIGURATION_SHA256",
        "recovery_digest": "RECOVERY_DIGEST",
        "expected_schema_before": "EXPECTED_SCHEMA_BEFORE",
        "expected_schema_after": "EXPECTED_SCHEMA_AFTER",
    }
    require(
        all(os.environ[key] == requested[name] for name, key in environment.items())
    )
    require(
        os.environ["SYNTHETIC_ONLY_CONFIRMED"] == "true"
        and requested["synthetic_only_confirmed"] is True
    )
    # Original 24-field request stays historical; current 26-field values must be explicit.
    current_runtime = RUNTIME_PUBLIC | {
        "SCRIBE_ENCOUNTER_CREATION_ENABLED": "true",
        "COMPANION_SESSION_PREP_ENABLED": "true",
    }
    require(parse(os.environ["STAGING_RUNTIME_PUBLIC_JSON"]) == current_runtime)
    require(
        request
        == {
            "workflow_control_sha": p["control"],
            "source_sha": p["candidate"],
            "image_digest": p["digest"],
            "approval_pr_number": 934,
            "dispatch": {
                "actor": "JohnClem",
                "triggering_actor": "JohnClem",
                "ref": "refs/heads/master",
                "ref_protected": True,
            },
            "synthetic_only_confirmed": True,
            "synthetic_evidence_sha256": requested["synthetic_evidence_sha256"],
            "staging_identity_fingerprints_sha256": requested[
                "staging_identity_fingerprints_sha256"
            ],
            "staging_effective_configuration_sha256": requested[
                "staging_effective_configuration_sha256"
            ],
            "staging_runtime_public": RUNTIME_PUBLIC,
            "expected_schema_before": requested["expected_schema_before"],
            "expected_schema_after": requested["expected_schema_after"],
            "expected_recovery_digest": requested["recovery_digest"],
        }
    )
    expected_jobs = {
        "authorization": "success",
        "backend": "success",
        "frontend": "success",
        "public_configuration": "success",
        "credentialless_image_build": "success",
        "publication": "success",
        "deployment": "failure",
    }
    require(attempt["jobs"] == expected_jobs)
    evidence = attempt["available_evidence"]
    require(
        evidence["build_archive_sha256"] == p["archive"]
        and evidence["build_image_id"] == p["image_id"]
    )
    require(
        evidence["frontend_config_sha256"]
        == p["frontend_sha256"]
        == "sha256:" + hashlib.sha256(frontend).hexdigest()
    )
    require(
        evidence["image_digest"] == p["digest"]
        and evidence["image_reference"]
        == "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@" + p["digest"]
    )
    require(
        all(
            evidence[key] is None
            for key in ["observed_source_sha", "schema_before", "schema_after"]
        )
    )
    require(
        publication
        == {
            "recorded_at": publication["recorded_at"],
            "workflow_control_sha": p["control"],
            "source_sha": p["candidate"],
            "image": {"digest": p["digest"], "reference": evidence["image_reference"]},
            "publication_outcome": "success",
            "recovered_from_run_id": None,
            "action": "publish_new_candidate",
            "run_id": p["run"],
            "run_attempt": p["attempt"],
        }
    )
    require(
        release["workflow_control_sha"] == p["control"]
        and release["source_sha"] == p["candidate"]
    )
    require(
        release["candidate_validation"]
        == {"run_id": p["run"], "run_attempt": p["attempt"], "reused": False}
    )
    require(
        release["image"] == publication["image"]
        and release["observed_source_sha"] is None
    )
    require(release["schema"] == {"before": None, "after": None})
    require(
        release["deployment_outcome"] == "failure"
        and release["release_contract_result"] == "failed"
    )
    rows = jobs["jobs"]
    require(
        jobs["total_count"] == len(rows) == 40
        and len({row["name"] for row in rows}) == len(rows)
    )
    require(
        all(
            row["run_id"] == p["run"]
            and row["run_attempt"] == p["attempt"]
            and row["head_sha"] == p["control"]
            and row["status"] == "completed"
            for row in rows
        )
    )
    shards = re.findall(rb"          - shard: ([a-z0-9-]+)\n", original_workflow)
    require(len(shards) == len(set(shards)) == 31)
    success = {
        "Authorize immutable staging candidate",
        "Candidate backend quality",
        "Candidate Frontend",
        "Candidate Backend",
        "Resolve reviewed public staging build configuration",
        "Build exact candidate without protected credentials",
        "Publish credential-free candidate archive",
    }
    success.update(f"Candidate backend tests ({name.decode()})" for name in shards)
    observed = {row["name"]: row for row in rows}
    require(
        set(observed)
        == success
        | {
            "Deploy exact candidate to persistent staging",
            "Record complete staging workflow attempt",
        }
    )
    require(all(observed[name]["conclusion"] == "success" for name in success))
    publisher = observed["Publish credential-free candidate archive"]
    require(publisher["id"] == 124954881082)
    for step_name in [
        "Verify and load candidate image archive without executing it",
        "Reverify candidate before immutable image publication",
        "Publish candidate SHA tag and resolve immutable digest",
        "Upload immutable publication evidence",
    ]:
        require(
            [
                step["conclusion"]
                for step in publisher["steps"]
                if step["name"] == step_name
            ]
            == ["success"]
        )
    deployment = observed["Deploy exact candidate to persistent staging"]
    require(deployment["id"] == 124956114501 and deployment["conclusion"] == "failure")
    require(
        [
            step["name"]
            for step in deployment["steps"]
            if step["conclusion"] == "failure"
        ]
        == [
            "Deploy candidate digest with protected-master controls",
            "Require staging health smoke to pass",
        ]
    )
    complete = observed["Record complete staging workflow attempt"]
    require(complete["id"] == 124956413581 and complete["conclusion"] == "failure")
    require(
        [step["name"] for step in complete["steps"] if step["conclusion"] == "failure"]
        == ["Require a complete successful staging attempt"]
    )
    return {
        "schema_version": 1,
        "action": "verify_original_successful_reader_publication_for_read_only_adoption",
        "original_run_id": p["run"],
        "original_run_attempt": p["attempt"],
        "original_controller_sha": p["control"],
        "candidate_sha": p["candidate"],
        "image_digest": p["digest"],
        "archive_sha256": p["archive"],
        "image_id": p["image_id"],
        "frontend_config_sha256": p["frontend_sha256"],
        "original_jobs": expected_jobs,
        "original_publication_still_successful": True,
        "original_deployment_still_failed": True,
        "all_original_candidate_jobs_verified": True,
        "original_runtime_keys": 24,
        "current_runtime_keys": 26,
        "current_configuration_still_requires_existing_full_fingerprint_gate": True,
        "registry_mutations": 0,
        "deployment_operations": 0,
    }


def main_reader039(selection: dict) -> None:
    require(
        selection
        == {
            "run_id": READER039["run"],
            "run_attempt": READER039["attempt"],
            "image_digest": READER039["digest"],
        }
    )
    original_workflow = subprocess.check_output(
        [
            "git",
            "show",
            READER039["control"] + ":.github/workflows/deploy_staging_candidate.yml",
        ]
    )
    attempt = checked_json(reader039_artifact("attempt"), "attempt.json")
    publication = checked_json(reader039_artifact("publication"), "publication.json")
    frontend = reader039_artifact("frontend")["staging-frontend-build.json"]
    files = reader039_artifact("release")
    reader039_artifact("image", download=False)
    result = validate_reader039(
        selection,
        attempt,
        publication,
        parse(files["request.json"]),
        parse(files["release.json"]),
        frontend,
        parse(gh(f"actions/runs/{READER039['run']}")),
        parse(
            gh(
                f"actions/runs/{READER039['run']}/attempts/{READER039['attempt']}/jobs?per_page=100"
            )
        ),
        original_workflow,
    )
    target = Path("staging-publication-recovery-evidence")
    target.mkdir(mode=0o700)
    (target / "recovery.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n"
    )
    outputs = {
        "recovery_verified": "true",
        "recovery_run_id": str(READER039["run"]),
        "recovery_run_attempt": str(READER039["attempt"]),
        "recovery_image_digest": READER039["digest"],
        "recovery_archive_sha256": READER039["archive"],
        "recovery_image_id": READER039["image_id"],
        "recovery_frontend_config_sha256": READER039["frontend_sha256"],
        "recovery_backend_result": "success",
        "recovery_frontend_result": "success",
        "recovery_build_result": "success",
    }
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")
    print(
        "Exact reader publication authenticated; fresh registry/configuration/source/schema/deployment gates remain required."
    )


def main() -> None:
    selection = parse(os.environ["PUBLICATION_RECOVERY_JSON"])
    if selection.get("run_id") == READER039["run"]:
        main_reader039(selection)
        return
    require(parse(os.environ["STAGING_RUNTIME_PUBLIC_JSON"]) == RUNTIME_PUBLIC)
    original_workflow = subprocess.check_output(
        ["git", "show", CONTROL + ":.github/workflows/deploy_staging_candidate.yml"]
    )
    attempt = checked_json(artifact("attempt"), "attempt.json")
    publication = checked_json(artifact("publication"), "publication.json")
    frontend_files = artifact("frontend")
    require(set(frontend_files) == {"staging-frontend-build.json"})
    artifact("image", download=False)
    result = validate(
        selection,
        attempt,
        publication,
        frontend_files["staging-frontend-build.json"],
        parse(gh(f"actions/runs/{RUN}")),
        parse(gh(f"actions/runs/{RUN}/attempts/{ATTEMPT}/jobs?per_page=100")),
        original_workflow,
        gh("actions/jobs/124200892393/logs", binary=True).decode("utf-8-sig"),
    )
    target = Path("staging-publication-recovery-evidence")
    target.mkdir(mode=0o700)
    (target / "recovery.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n"
    )
    outputs = {
        "recovery_verified": "true",
        "recovery_run_id": str(RUN),
        "recovery_run_attempt": str(ATTEMPT),
        "recovery_image_digest": DIGEST,
        "recovery_archive_sha256": ARCHIVE,
        "recovery_image_id": IMAGE_ID,
        "recovery_frontend_config_sha256": result["frontend_config_sha256"],
        "recovery_backend_result": "success",
        "recovery_frontend_result": "success",
        "recovery_build_result": "success",
    }
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")
    print(
        "Original failed publication authenticated; fresh registry/configuration/deployment gates still required."
    )


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ):
        raise SystemExit("canonical_publication_recovery_refused") from None
