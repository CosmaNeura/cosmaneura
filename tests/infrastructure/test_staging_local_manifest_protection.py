"""Exercise publication completion after invented ACR metadata readbacks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("field", "value", "succeeds"),
    (
        ("deleteEnabled", False, True),
        ("deleteEnabled", True, False),
        ("writeEnabled", True, False),
        ("readEnabled", False, False),
        ("listEnabled", False, False),
        ("digest", "sha256:" + "b" * 64, False),
    ),
)
def test_success_receipt_requires_verified_manifest_protection(
    tmp_path, field, value, succeeds
):
    digest = "sha256:" + "a" * 64
    manifest = {
        "digest": digest,
        "changeableAttributes": {
            "deleteEnabled": False,
            "writeEnabled": False,
            "readEnabled": True,
            "listEnabled": True,
        },
    }
    if field == "digest":
        manifest[field] = value
    else:
        manifest["changeableAttributes"][field] = value
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    publisher = (ROOT / "infra-staging/publish-local.sh").read_text()
    phase = publisher[publisher.index("PHASE=lock_and_reverify_candidate_manifest\n") :]
    script = (
        """set -euo pipefail
REGISTRY_NAME=cosmaneurastagingregistry
REGISTRY_REPOSITORY=cosmaneura/cosmaneura
CANDIDATE_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
IMAGE_DIGEST=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
JQ=/usr/bin/jq
EVIDENCE_PATH=success.json
bound_acr() {
    if [[ "$*" == *"show-metadata"* ]]; then cat manifest.json; fi
}
write_evidence() { printf '%s' "$*" > success.json; }
"""
        + phase
    )
    result = subprocess.run(
        ["/bin/bash", "-c", script], cwd=tmp_path, capture_output=True
    )
    assert (result.returncode == 0) is succeeds
    assert (tmp_path / "success.json").exists() is succeeds
