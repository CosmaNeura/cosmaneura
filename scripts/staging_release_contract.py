"""Pure validation for the reviewed, Actions-off combined staging release.

Public runtime and per-workload recovery are bound to the build plan. The
publication receipt binds that same plan to the immutable image. No credentials
are read here and this module never calls a provider.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, cast

REGISTRY = "cosmaneurastagingregistry.azurecr.io"
IMAGE = REGISTRY + "/cosmaneura/cosmaneura"
APPS = {
    "cosmaneura-staging-app",
    "cosmaneura-staging-worker",
    "cosmaneura-staging-scheduler",
}
FLAGS = {
    "SCRIBE_WORKFLOW_ENABLED",
    "SCRIBE_CAPTURE_ENABLED",
    "SCRIBE_CAPTURE_NOTES_ENABLED",
    "SCRIBE_CAPTURE_LONG_VISITS_ENABLED",
    "SCRIBE_CAPTURE_WORKING_TRANSCRIPTS_ENABLED",
    "SCRIBE_CAPTURE_RECOVERY_ENABLED",
    "SCRIBE_CAPTURE_SOURCE_ENABLED",
    "SCRIBE_ENCOUNTER_PREP_ENABLED",
    "SESSION_PREP_PROVIDER_ENABLED",
}
FIELDS = FLAGS | {
    "SCRIBE_SERVICE_BASE_URL",
    "SCRIBE_SERVICE_SCOPE",
    "SCRIBE_MANAGED_IDENTITY_CLIENT_ID",
    "SCRIBE_SERVICE_TIMEOUT_SECONDS",
    "SCRIBE_CAPTURE_UPLOAD_ORIGINS",
    "AGENT_API_SCRIBE_RECEIVER_CLIENT_ID",
}
COMPANION_FLAGS = {
    "COMPANION_ENCOUNTER_READ_ENABLED",
    "COMPANION_RECORD_READ_ENABLED",
    "COMPANION_APPOINTMENT_SETUP_ENABLED",
}
COMPANION_FIELDS = COMPANION_FLAGS | {"AGENT_API_COMPANION_CLIENT_ID"}
PRESERVED_WORKFLOW_FLAGS = {
    "SCRIBE_COMBINED_SESSION_PREPARATION_ENABLED",
    "SCRIBE_NATIVE_REQUEST_TIMING",
    "SCRIBE_WORKFLOW_IDENTITY_REUSE_ENABLED",
    "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED",
    "SESSION_PREP_HEALTH_ALERTS_ENABLED",
}
PRESERVED_PREPARATION_FLAGS = {
    "SCRIBE_ENCOUNTER_CREATION_ENABLED",
    "COMPANION_SESSION_PREP_ENABLED",
}
NATIVE_FLAGS = {
    "SCRIBE_NATIVE_WORKLOAD_ENABLED",
    "SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED",
    "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED",
    "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED",
}
NATIVE_FIELDS = NATIVE_FLAGS | {
    "SCRIBE_NATIVE_WORKLOAD_AUDIENCE",
    "SCRIBE_NATIVE_WORKLOAD_CLIENT_ID",
}
ACTIVATION_FIELDS = {
    "SCRIBE_AUTOMATIC_ACTIVATION_ENABLED",
    "SCRIBE_ACTIVATION_PRACTICE_REFS",
}
CANCELLATION_FLAGS = {
    "SCRIBE_CANCELLATION_ENABLED",
    "SCRIBE_CANCELLATION_LEDGER_READY",
}
CANCELLATION_FIELDS = CANCELLATION_FLAGS | {
    "SCRIBE_CANCELLATION_GRACE_DAYS",
    "SCRIBE_CANCELLATION_PHI_SCOPE",
    "SCRIBE_CANCELLATION_POLICY_SHA256",
}
NATIVE_BINDING_FIELDS = {
    "ENTRA_TENANT_ID",
    "AGENT_API_SOVEREIGN_CLIENT_IDS",
    "AUTHORITY_CLERK_ISSUER",
    "MOUNT_CONTEXT_TOKEN_TTL_SECONDS",
    "MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS",
    "MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS",
}


def canonical_uuid(value: str) -> bool:
    return bool(
        re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value)
        and value != "00000000-0000-0000-0000-000000000000"
    )


def validate_native_groups(runtime: dict[str, str]) -> None:
    """Complete, bounded optional groups never grant omitted capabilities."""
    for group in (NATIVE_FIELDS, ACTIVATION_FIELDS, CANCELLATION_FIELDS):
        require(not (group & runtime.keys()) or group <= runtime.keys())
    if NATIVE_FIELDS <= runtime.keys():
        require(all(runtime[key] in {"true", "false"} for key in NATIVE_FLAGS))
        workload = runtime["SCRIBE_NATIVE_WORKLOAD_ENABLED"] == "true"
        context = runtime["SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED"] == "true"
        visits = runtime["SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED"] == "true"
        actions = runtime["SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED"] == "true"
        require(not context or workload)
        require(not visits or (workload and context))
        require(not actions or (workload and context and visits))
        for key in NATIVE_FIELDS - NATIVE_FLAGS:
            require(canonical_uuid(runtime[key]) if workload else runtime[key] == "")
    if ACTIVATION_FIELDS <= runtime.keys():
        enabled = runtime["SCRIBE_AUTOMATIC_ACTIVATION_ENABLED"]
        require(enabled in {"true", "false"})
        cohort = runtime["SCRIBE_ACTIVATION_PRACTICE_REFS"]
        refs = cohort.split(",") if cohort else []
        require(len(refs) <= 32 and len(set(refs)) == len(refs))
        require(
            all(
                re.fullmatch(r"practice_[0-7][0-9a-hjkmnp-tv-z]{25}", ref)
                for ref in refs
            )
        )
        if enabled == "true":
            require(refs and runtime.get("SCRIBE_NATIVE_WORKLOAD_ENABLED") == "true")
    if CANCELLATION_FIELDS <= runtime.keys():
        require(all(runtime[key] in {"true", "false"} for key in CANCELLATION_FLAGS))
        grace = runtime["SCRIBE_CANCELLATION_GRACE_DAYS"]
        scope = runtime["SCRIBE_CANCELLATION_PHI_SCOPE"]
        policy = runtime["SCRIBE_CANCELLATION_POLICY_SHA256"]
        require(not grace or (re.fullmatch(r"[3-8][0-9]|90", grace) is not None))
        require(scope in {"", "core-and-scribe-clinical-records"})
        require(not policy or re.fullmatch(r"[0-9a-f]{64}", policy))
        if runtime["SCRIBE_CANCELLATION_ENABLED"] == "true":
            require(
                runtime["SCRIBE_CANCELLATION_LEDGER_READY"] == "true"
                and runtime.get("SCRIBE_NATIVE_WORKLOAD_ENABLED") == "true"
                and grace
                and scope
                and policy
            )


def validate_native_binding(configuration: dict[str, str]) -> None:
    """Validate only public deployment bindings, without loading the application."""
    fields = NATIVE_FIELDS | ACTIVATION_FIELDS | CANCELLATION_FIELDS
    require(
        type(configuration) is dict
        and set(configuration) <= fields | NATIVE_BINDING_FIELDS
    )
    require(
        all(
            type(value) is str and len(value) < 2048 for value in configuration.values()
        )
    )
    validate_native_groups(configuration)
    if configuration.get("SCRIBE_NATIVE_WORKLOAD_ENABLED") != "true":
        return
    require(NATIVE_BINDING_FIELDS <= configuration.keys())
    require(canonical_uuid(configuration["ENTRA_TENANT_ID"]))
    # These are the unextended Sovereign tool callers. Native auth selects its
    # own singleton for both policies and compares their verified client IDs;
    # it does not require equality with the legacy capture caller or audience.
    tools = configuration["AGENT_API_SOVEREIGN_CLIENT_IDS"].split(",")
    require(tools and all(canonical_uuid(value) for value in tools))
    require(configuration["SCRIBE_NATIVE_WORKLOAD_CLIENT_ID"] not in tools)
    require(
        configuration["AUTHORITY_CLERK_ISSUER"]
        == "https://resolved-emu-53.clerk.accounts.dev"
    )
    ttl, refresh, maximum = (
        configuration[key]
        for key in (
            "MOUNT_CONTEXT_TOKEN_TTL_SECONDS",
            "MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS",
            "MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS",
        )
    )
    require(
        all(
            re.fullmatch(r"[1-9][0-9]{0,8}", value) for value in (ttl, refresh, maximum)
        )
    )
    require(int(ttl) >= 300 and 60 <= int(refresh) < int(ttl) <= int(maximum))


def require(value: object) -> None:
    if not value:
        raise ValueError("staging_release_contract_refused")


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def bound_json(filename: str, digest: str):
    path = Path(filename)
    require(
        path.is_absolute()
        and path.resolve() == path
        and path.is_relative_to("/private/tmp")
    )
    require(re.fullmatch(r"[0-9a-f]{64}", digest))
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        require(before.st_uid == os.geteuid() and not before.st_mode & 0o022)
        require(0 < before.st_size <= 65536)
        raw = stream.read(65537)
        after = os.fstat(stream.fileno())
        fields = (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        require(
            all(
                getattr(before, key) == getattr(after, key) == getattr(path.stat(), key)
                for key in fields
            )
        )
    require(hashlib.sha256(raw).hexdigest() == digest)
    return json.loads(raw, object_pairs_hook=unique)


def validate_runtime(runtime: dict[str, str]) -> None:
    # Historical Scribe-only plans remain valid. Companion admission is an
    # explicit, complete group, including an empty caller for full withdrawal.
    # Optional groups are independent and complete. Historical plans remain
    # valid; absent groups never synthesize defaults or admit inherited values.
    require(type(runtime) is dict and FIELDS <= runtime.keys())
    remaining = set(runtime) - FIELDS
    for group in (
        COMPANION_FIELDS,
        PRESERVED_WORKFLOW_FLAGS,
        PRESERVED_PREPARATION_FLAGS,
        NATIVE_FIELDS,
        ACTIVATION_FIELDS,
        CANCELLATION_FIELDS,
    ):
        if remaining & group:
            require(group <= remaining)
            remaining -= group
    require(not remaining)
    require(all(type(value) is str and len(value) < 2048 for value in runtime.values()))
    require(all(runtime[key] in {"true", "false"} for key in FLAGS))
    validate_native_groups(runtime)
    if PRESERVED_WORKFLOW_FLAGS <= runtime.keys():
        require(
            all(runtime[key] in {"true", "false"} for key in PRESERVED_WORKFLOW_FLAGS)
        )
    if PRESERVED_PREPARATION_FLAGS <= runtime.keys():
        require(
            all(
                runtime[key] in {"true", "false"} for key in PRESERVED_PREPARATION_FLAGS
            )
        )
    if COMPANION_FIELDS <= runtime.keys():
        require(all(runtime[key] in {"true", "false"} for key in COMPANION_FLAGS))
        caller = runtime["AGENT_API_COMPANION_CLIENT_ID"]
        if caller:
            require(
                re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", caller)
            )
            require(caller != "00000000-0000-0000-0000-000000000000")
            require(caller != runtime["AGENT_API_SCRIBE_RECEIVER_CLIENT_ID"])
        else:
            require(all(runtime[key] == "false" for key in COMPANION_FLAGS))
    require(
        all(
            runtime[key] == "true"
            for key in (
                "SCRIBE_WORKFLOW_ENABLED",
                "SCRIBE_CAPTURE_ENABLED",
                "SCRIBE_CAPTURE_NOTES_ENABLED",
                "SESSION_PREP_PROVIDER_ENABLED",
            )
        )
    )
    require(
        runtime["SCRIBE_SERVICE_BASE_URL"]
        == "https://cosmaneura-staging-scribe-api.internal.politesmoke-7989878f.northcentralus.azurecontainerapps.io"
    )
    require(
        runtime["SCRIBE_SERVICE_SCOPE"]
        == "api://57264fe1-6a4a-4946-8412-e0be18805977/.default"
    )
    for key in (
        "SCRIBE_MANAGED_IDENTITY_CLIENT_ID",
        "AGENT_API_SCRIBE_RECEIVER_CLIENT_ID",
    ):
        require(
            re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", runtime[key])
        )
    require(runtime["SCRIBE_SERVICE_TIMEOUT_SECONDS"] == "60")
    origins = runtime["SCRIBE_CAPTURE_UPLOAD_ORIGINS"].split(",")
    require(0 < len(origins) <= 4 and len(set(origins)) == len(origins))
    require(
        all(
            re.fullmatch(r"https://[a-z0-9]+\.blob\.core\.windows\.net", value)
            for value in origins
        )
    )


def validate_recovery(recovery: dict[str, dict[str, str]]) -> None:
    require(type(recovery) is dict and set(recovery) == APPS)
    for name, value in recovery.items():
        require(type(value) is dict and set(value) == {"image", "revision"})
        require(
            type(value["revision"]) is str
            and re.fullmatch(re.escape(name) + r"--[a-z0-9-]+", value["revision"])
        )
        require(
            type(value["image"]) is str
            and re.fullmatch(
                re.escape(REGISTRY)
                + r"/(?:cosmaneura/cosmaneura|scribe-beta/core)@sha256:[0-9a-f]{64}",
                value["image"],
            )
        )


def resolve_publication(
    plan: dict[str, Any], plan_digest: str, publication: dict[str, Any]
) -> dict[str, Any]:
    """Resolve an explicit reuse binding without rewriting publication history.

    Source authentication and the fresh plan clock remain the deployer's gates.
    Recovery can still consume retained bindings after their clocks expire.
    """
    if publication.get("schema_version") != 2:
        return publication
    require(
        set(publication)
        == {
            "schema_version",
            "action",
            "plan_sha256",
            "original_plan",
            "original_publication",
        }
    )
    require(type(publication["schema_version"]) is int)
    require(publication["action"] == "reuse_published_staging_image")
    require(publication["plan_sha256"] == plan_digest)
    values = []
    for name in ("original_plan", "original_publication"):
        spec = publication[name]
        require(type(spec) is dict and set(spec) == {"path", "sha256"})
        require(type(spec["path"]) is str and type(spec["sha256"]) is str)
        values.append(bound_json(cast(str, spec["path"]), cast(str, spec["sha256"])))
    original, published = values
    # No transitive receipt chains or implicit changes to the access model,
    # recovery baseline, schema, frontend, source archive, or build materials.
    require(published.get("schema_version") == 1)
    deployment_inputs(original, publication["original_plan"]["sha256"], published)
    for key in (
        "version",
        "candidate_sha",
        "candidate_pr",
        "source_archive_sha256",
        "github_user_sha256",
        "engine",
        "release",
    ):
        require(plan[key] == original[key])
    for key in ("frontend", "frontend_origin", "rehearsal", "native_archive"):
        require(plan[key]["sha256"] == original[key]["sha256"])
    created = datetime.fromisoformat(original["created_at"])
    expires = datetime.fromisoformat(original["expires_at"])
    recorded = datetime.fromisoformat(published["recorded_at"].replace("Z", "+00:00"))
    require(
        all(value.utcoffset() is not None for value in (created, expires, recorded))
    )
    require(
        created <= recorded < expires
        and 0 < (expires - created).total_seconds() <= 7200
    )
    return published


def deployment_inputs(
    plan: dict[str, Any], plan_digest: str, publication: dict[str, Any]
) -> dict[str, Any]:
    require(plan["version"] == 2)
    reused = publication.get("schema_version") == 2
    publication = resolve_publication(plan, plan_digest, publication)
    release = plan["release"]
    validate_runtime(release["runtime_public"])
    validate_recovery(release["recovery"])
    require(publication["schema_version"] == 1 and publication["complete"] is True)
    require(publication["outcome"] == "success" and publication["phase"] == "complete")
    require(
        publication["publication_started"] is True
        and publication["production_operation"] is False
    )
    require(publication["candidate_sha"] == plan["candidate_sha"])
    if not reused:
        require(publication["controller_sha"] == plan["controller_sha"])
        require(publication["build_plan_sha256"] == plan_digest)
    digest = publication["image_digest"]
    require(type(digest) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", digest))
    require(publication["image_reference"] == IMAGE + "@" + digest)
    return {
        "candidate_sha": plan["candidate_sha"],
        "candidate_pr": plan["candidate_pr"],
        "image_digest": digest,
        **release,
    }


def verify_recovery(
    recovery: dict[str, dict[str, str]], observed: list[dict[str, Any]]
) -> None:
    validate_recovery(recovery)
    require(type(observed) is list and len(observed) == len(APPS))
    require({row["name"] for row in observed} == APPS)
    for row in observed:
        expected = recovery[row["name"]]
        require(row["readyRevisionImage"] == expected["image"])
        require(row["latestReadyRevision"] == expected["revision"])


def attempt_context(env: dict[str, str]) -> dict[str, Any]:
    """Bind evidence to this authorized job, never to downloaded assertions."""
    request = {
        "workflow_control_sha": env["WORKFLOW_SHA"],
        "source_sha": env["CANDIDATE_SHA_INPUT"],
        "image_digest": env["IMAGE_DIGEST"],
        "approval_pr_number": int(env["APPROVAL_PR_NUMBER_INPUT"]),
        "dispatch": {
            "actor": env["GITHUB_ACTOR"],
            "triggering_actor": env["GITHUB_TRIGGERING_ACTOR"],
            "ref": env["GITHUB_REF"],
            "ref_protected": env["GITHUB_REF_PROTECTED"] == "true",
        },
        "synthetic_only_confirmed": env["SYNTHETIC_ONLY_CONFIRMED_INPUT"] == "true",
        "synthetic_evidence_sha256": env["SYNTHETIC_EVIDENCE_SHA256_INPUT"],
        "staging_identity_fingerprints_sha256": env[
            "STAGING_IDENTITY_FINGERPRINTS_SHA256_INPUT"
        ],
        "staging_effective_configuration_sha256": env[
            "STAGING_EFFECTIVE_CONFIGURATION_SHA256_INPUT"
        ],
        "staging_runtime_public": json.loads(
            env["STAGING_RUNTIME_PUBLIC_JSON"], object_pairs_hook=unique
        ),
        "expected_schema_before": env["EXPECTED_SCHEMA_BEFORE_INPUT"],
        "expected_schema_after": env["EXPECTED_SCHEMA_AFTER_INPUT"],
        "expected_recovery_digest": env["RECOVERY_DIGEST_INPUT"],
    }
    context = {
        "request": request,
        "repository": env["GITHUB_REPOSITORY"],
        "repository_id": int(env["GITHUB_REPOSITORY_ID"]),
        "run_id": int(env["GITHUB_RUN_ID"]),
        "run_attempt": int(env["GITHUB_RUN_ATTEMPT"]),
        "validation": {
            "run_id": int(env["VALIDATION_RUN_ID"]),
            "run_attempt": int(env["VALIDATION_RUN_ATTEMPT"]),
            "reused": env["RECOVERY_RUN_ID"] != "",
        },
    }
    validate_attempt_context(context)
    return context


def validate_attempt_context(context: dict[str, Any]) -> None:
    request = context["request"]
    require(context["repository"] == "CosmaNeura/cosmaneura")
    require(context["repository_id"] == 881573489)
    require(
        request["dispatch"]
        == {
            "actor": "JohnClem",
            "triggering_actor": "JohnClem",
            "ref": "refs/heads/master",
            "ref_protected": True,
        }
    )
    require(request["synthetic_only_confirmed"] is True)
    require(
        type(request["approval_pr_number"]) is int and request["approval_pr_number"] > 0
    )
    for field in ("workflow_control_sha", "source_sha"):
        require(re.fullmatch(r"[0-9a-f]{40}", request[field]))
    for field in (
        "image_digest",
        "synthetic_evidence_sha256",
        "staging_identity_fingerprints_sha256",
        "staging_effective_configuration_sha256",
        "expected_recovery_digest",
    ):
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", request[field]))
    for field in ("expected_schema_before", "expected_schema_after"):
        require(re.fullmatch(r"[0-9a-f]{12}", request[field]))
    validate_runtime(request["staging_runtime_public"])
    for value in (
        context["run_id"],
        context["run_attempt"],
        context["validation"]["run_id"],
        context["validation"]["run_attempt"],
    ):
        require(type(value) is int and value > 0)
    require(type(context["validation"]["reused"]) is bool)
    if not context["validation"]["reused"]:
        require(context["validation"]["run_id"] == context["run_id"])
        require(context["validation"]["run_attempt"] == context["run_attempt"])


def validate_attempt_artifact_metadata(
    metadata: dict[str, Any], context: dict[str, Any]
) -> None:
    validate_attempt_context(context)
    request = context["request"]
    require(type(metadata["id"]) is int and metadata["id"] > 0)
    require(
        type(metadata["size_in_bytes"]) is int
        and 0 < metadata["size_in_bytes"] <= 1048576
    )
    require(metadata["expired"] is False)
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", metadata["digest"]))
    require(
        metadata["name"]
        == f"canonical-staging-release-{request['source_sha']}-{context['run_id']}-{context['run_attempt']}"
    )
    workflow = metadata["workflow_run"]
    require(
        all(
            type(workflow[key]) is int
            for key in ("id", "repository_id", "head_repository_id")
        )
    )
    require(workflow["id"] == context["run_id"])
    require(workflow["repository_id"] == context["repository_id"])
    require(workflow["head_repository_id"] == context["repository_id"])
    require(workflow["head_branch"] == "master")
    require(workflow["head_sha"] == request["workflow_control_sha"])


def select_attempt_artifact(
    listing: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Require a complete authenticated same-run list and one exact producer name.

    The producer uses overwrite:false and the consumer requires deploy success;
    a competing artifact with this name makes that mandatory producer fail.
    No artifact ID or digest needs to traverse GitHub's screened job outputs.
    """
    validate_attempt_context(context)
    rows = cast(list[dict[str, Any]], listing["artifacts"])
    require(type(rows) is list and type(listing["total_count"]) is int)
    require(len(rows) == listing["total_count"] and len(rows) <= 100)
    require(len({row["id"] for row in rows}) == len(rows))
    name = f"canonical-staging-release-{context['request']['source_sha']}-{context['run_id']}-{context['run_attempt']}"
    matches = [row for row in rows if row["name"] == name]
    require(len(matches) == 1)
    validate_attempt_artifact_metadata(matches[0], context)
    return matches[0]


def bounded_attempt_file(filename: str, limit: int) -> bytes:
    path = Path(filename).absolute()
    require(path.resolve() == path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        require(0 < before.st_size <= limit)
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        require(
            all(
                getattr(before, key) == getattr(after, key) == getattr(path.stat(), key)
                for key in fields
            )
        )
    require(len(raw) == before.st_size)
    return raw


def validate_attempt_bundle(
    metadata: dict[str, Any], archive: bytes, context: dict[str, Any]
) -> dict[str, Any]:
    """Validate immutable same-run deployment evidence without extracting a ZIP."""
    validate_attempt_artifact_metadata(metadata, context)
    require(len(archive) == metadata["size_in_bytes"])
    require("sha256:" + hashlib.sha256(archive).hexdigest() == metadata["digest"])
    names = {
        "request.json",
        "release.json",
        "azure-identity.json",
        "workflow-result.json",
        "SHA256SUMS",
    }
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        entries = bundle.infolist()
        require(
            len(entries) == len(names) and {row.filename for row in entries} == names
        )
        require(sum(row.file_size for row in entries) <= 524288)
        require(
            all(
                0 < row.file_size <= 131072
                and not row.is_dir()
                and not stat.S_ISLNK(row.external_attr >> 16)
                and not row.flag_bits & 1
                for row in entries
            )
        )
        files = {row.filename: bundle.read(row) for row in entries}
    checksums = files["SHA256SUMS"].decode("ascii").splitlines()
    require(len(checksums) == 4)
    expected = {
        f"{hashlib.sha256(files[name]).hexdigest()}  {name}"
        for name in names - {"SHA256SUMS"}
    }
    require(set(checksums) == expected)
    docs = {
        name: json.loads(raw, object_pairs_hook=unique)
        for name, raw in files.items()
        if name.endswith(".json")
    }
    request, release, result = (
        docs["request.json"],
        docs["release.json"],
        docs["workflow-result.json"],
    )
    require(
        json.dumps(request, sort_keys=True)
        == json.dumps(context["request"], sort_keys=True)
    )
    github = {key: context[key] for key in ("repository", "run_id", "run_attempt")}
    github.update(request["dispatch"])
    require(
        json.dumps(release["github"], sort_keys=True)
        == json.dumps(github, sort_keys=True)
    )
    require(result["job_status"] == "success")
    require(type(result["run_id"]) is int and type(result["run_attempt"]) is int)
    require(result["workflow_control_sha"] == request["workflow_control_sha"])
    require(
        result["run_id"] == context["run_id"]
        and result["run_attempt"] == context["run_attempt"]
    )
    for field in (
        "workflow_control_sha",
        "source_sha",
        "approval_pr_number",
        "synthetic_only_confirmed",
        "synthetic_evidence_sha256",
    ):
        require(release[field] == request[field])
    require(
        type(release["approval_pr_number"]) is int
        and release["synthetic_only_confirmed"] is True
    )
    require(release["observed_source_sha"] == request["source_sha"])
    require(
        json.dumps(release["candidate_validation"], sort_keys=True)
        == json.dumps(context["validation"], sort_keys=True)
    )
    require(
        release["candidate_tests"]
        == dict.fromkeys(
            (
                "backend",
                "frontend",
                "public_configuration",
                "credentialless_image_build",
            ),
            "success",
        )
    )
    require(
        release["provenance_checks"]
        == dict.fromkeys(
            (
                "authorization",
                "pre_credentials",
                "publication",
                "azure_identity",
                "pre_migration_and_azure_mutation",
            ),
            "success",
        )
    )
    require(
        release["image"]
        == {
            "digest": request["image_digest"],
            "reference": IMAGE + "@" + request["image_digest"],
        }
    )
    require(
        release["schema"]
        == {
            "before": request["expected_schema_before"],
            "after": request["expected_schema_after"],
        }
    )
    require(release["recovery_digest"] == request["expected_recovery_digest"])
    require(
        release["deployment_outcome"] == "success"
        and release["release_contract_result"] == "passed"
    )
    require(release["smoke"]["healthcheck"] == {"http_status": 200, "result": "passed"})
    require(release["staging_identity"] == docs["azure-identity.json"])
    for key, field in (
        ("staging_identity_fingerprints", "staging_identity_fingerprints_sha256"),
        ("staging_effective_configuration", "staging_effective_configuration_sha256"),
    ):
        require(
            release[key]
            == {"expected_sha256": request[field], "observed_sha256": request[field]}
        )
    configuration = release["observed_configuration"]
    require(configuration["deployment_blockers"] == [])
    require(configuration["effective_configuration_preserved"] is True)
    require(
        configuration["effective_configuration_sha256"]
        == request["staging_effective_configuration_sha256"]
    )
    require(
        configuration["identity_fingerprint_set_sha256"]
        == request["staging_identity_fingerprints_sha256"]
    )
    require(
        configuration["expected_identity_fingerprint_set_sha256"]
        == request["staging_identity_fingerprints_sha256"]
    )
    require(configuration["flags"]["SENTRY_STAGING_ENABLED"] == "false")
    require(configuration["flags"]["ZOOM_WATCHDOG_ENABLED"] == "false")
    before = cast(list[dict[str, Any]], release["workloads"]["before"])
    after = cast(list[dict[str, Any]], release["workloads"]["after"])
    for rows in (before, after):
        require(type(rows) is list and len(rows) == 3)
        require({row["name"] for row in rows} == APPS)
    for row in after:
        require(row["readyRevisionImage"] == IMAGE + "@" + request["image_digest"])
        require(row["publicConfig"]["SENTRY_STAGING_ENABLED"] == "false")
        require(row["publicConfig"]["ZOOM_WATCHDOG_ENABLED"] == "false")
    return {
        "configuration": configuration,
        "workloads_before": before,
        "workloads_after": after,
        "artifact_id": metadata["id"],
        "artifact_digest": metadata["digest"],
    }


def main(argv):
    if argv and argv[0] in {"--complete-attempt-select", "--complete-attempt-validate"}:
        require(len(argv) == (3 if argv[0] == "--complete-attempt-select" else 4))
        context = attempt_context(dict(os.environ))
        document = json.loads(
            bounded_attempt_file(argv[1], 1048576), object_pairs_hook=unique
        )
        if argv[0] == "--complete-attempt-select":
            output = select_attempt_artifact(document, context)
        else:
            output = validate_attempt_bundle(
                document, bounded_attempt_file(argv[2], 1048576), context
            )
        with open(argv[-1], "x", encoding="utf-8") as stream:
            json.dump(output, stream, sort_keys=True, separators=(",", ":"))
        return
    if argv in (["--validate-runtime-public"], ["--validate-native-binding"]):
        raw = sys.stdin.buffer.read(65537)
        require(0 < len(raw) <= 65536)
        runtime = json.loads(raw, object_pairs_hook=unique)
        if argv == ["--validate-native-binding"]:
            validate_native_binding(runtime)
        else:
            validate_runtime(runtime)
            print(json.dumps(runtime, sort_keys=True, separators=(",", ":")))
        return
    require(len(argv) in {3, 5, 7})
    plan = bound_json(argv[1], argv[2])
    if argv[0] == "--reuse-publication":
        require(len(argv) == 7)
        publication = {
            "schema_version": 2,
            "action": "reuse_published_staging_image",
            "plan_sha256": argv[2],
            "original_plan": {"path": argv[3], "sha256": argv[4]},
            "original_publication": {"path": argv[5], "sha256": argv[6]},
        }
        deployment_inputs(plan, argv[2], publication)
        print(json.dumps(publication, sort_keys=True))
    elif argv[0] == "--deployment-inputs":
        require(len(argv) == 5)
        print(
            json.dumps(deployment_inputs(plan, argv[2], bound_json(argv[3], argv[4])))
        )
    elif argv[0] == "--verify-recovery":
        require(len(argv) == 3)
        verify_recovery(
            plan["release"]["recovery"], json.load(sys.stdin, object_pairs_hook=unique)
        )
    else:
        require(False)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile):
        raise SystemExit("staging_release_contract_refused") from None
