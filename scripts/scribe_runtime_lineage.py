"""Compare recorded repair lineage with pinned source and a fresh runtime file.

Local files and read-only Git plumbing only; never contacts a live system.
Successful stdout contains hashes and commit IDs only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from scripts.scribe_runtime_pin import SCRIBE_SOURCE_REVISION

REPAIR_REVISION = "0784c2451f42da7fb46abafddc3db18a5ca6bbda"
FOLLOWUP_REVISION = "91f11b5fe823da6d2d31cfe3807adab355795578"
REPAIR_SHA256 = "b183e873ae9d65deb29a675522a0fa4fedcc555c4100fe6f7c409d76320c6947"
RUNTIME_SHA256 = "f133c35e9d93483f040fc32ce919adaedc5fb0047bcc05484b1886bc4c0688e8"
MODULE_PATH = "services/api/src/cosmaneura_scribe/provider_native_workflow_transport.py"
PATCH_LABEL = "com.cosmaneura.scribe-api-patch-sha256"


class LineageRejected(ValueError):
    """A required recorded-input, lineage or runtime condition failed."""


def git(repository: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repository), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise LineageRejected("scribe_lineage_git_rejected")
    return result.stdout


def verify(
    repository: Path, recorded_labels: Path, runtime_module: Path
) -> tuple[str, ...]:
    labels = json.loads(recorded_labels.read_text())
    if type(labels) is not dict or labels.get(PATCH_LABEL) != REPAIR_SHA256:
        raise LineageRejected("scribe_lineage_recorded_label_rejected")
    repair = git(repository, "show", f"{REPAIR_REVISION}:{MODULE_PATH}")
    if hashlib.sha256(repair).hexdigest() != REPAIR_SHA256:
        raise LineageRejected("scribe_lineage_repair_hash_rejected")
    git(
        repository,
        "merge-base",
        "--is-ancestor",
        REPAIR_REVISION,
        SCRIBE_SOURCE_REVISION,
    )
    touches = (
        git(
            repository,
            "log",
            "--format=%H",
            f"{REPAIR_REVISION}..{SCRIBE_SOURCE_REVISION}",
            "--",
            MODULE_PATH,
        )
        .decode()
        .splitlines()
    )
    if touches != [FOLLOWUP_REVISION]:
        raise LineageRejected("scribe_lineage_followup_rejected")
    pinned = git(repository, "show", f"{SCRIBE_SOURCE_REVISION}:{MODULE_PATH}")
    if hashlib.sha256(pinned).hexdigest() != RUNTIME_SHA256:
        raise LineageRejected("scribe_lineage_pinned_hash_rejected")
    if runtime_module.is_symlink() or not runtime_module.is_file():
        raise LineageRejected("scribe_lineage_runtime_missing")
    if hashlib.sha256(runtime_module.read_bytes()).hexdigest() != RUNTIME_SHA256:
        raise LineageRejected("scribe_lineage_runtime_hash_rejected")
    return (
        REPAIR_SHA256,
        REPAIR_REVISION,
        FOLLOWUP_REVISION,
        SCRIBE_SOURCE_REVISION,
        RUNTIME_SHA256,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repository", type=Path, required=True)
    parser.add_argument("--recorded-labels", type=Path, required=True)
    parser.add_argument("--runtime-module", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(
            args.source_repository, args.recorded_labels, args.runtime_module
        )
    except (LineageRejected, OSError, ValueError, subprocess.TimeoutExpired):
        # Do not echo arbitrary labels, Git errors, file paths or file content.
        return 1
    print("\n".join(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
