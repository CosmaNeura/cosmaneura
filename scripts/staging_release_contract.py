"""Pure validation for the reviewed, Actions-off combined staging release.

Public runtime and per-workload recovery are bound to the build plan. The
publication receipt binds that same plan to the immutable image. No credentials
are read here and this module never calls a provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

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
    "SESSION_PREP_PROVIDER_CLERK_SUBJECTS",
}


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
    require(type(runtime) is dict and set(runtime) == FIELDS)
    require(all(type(value) is str and len(value) < 2048 for value in runtime.values()))
    require(all(runtime[key] in {"true", "false"} for key in FLAGS))
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
    subjects = runtime["SESSION_PREP_PROVIDER_CLERK_SUBJECTS"].split(",")
    require(0 < len(subjects) <= 10 and len(set(subjects)) == len(subjects))
    require(all(re.fullmatch(r"user_[A-Za-z0-9]{10,64}", value) for value in subjects))


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


def deployment_inputs(
    plan: dict[str, Any], plan_digest: str, publication: dict[str, Any]
) -> dict[str, Any]:
    require(plan["version"] == 2)
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


def main(argv):
    require(len(argv) in {3, 5})
    plan = bound_json(argv[1], argv[2])
    if argv[0] == "--deployment-inputs":
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
    except (ValueError, KeyError, TypeError, OSError):
        raise SystemExit("staging_release_contract_refused") from None
