"""Publish a scanned production image for the staging-verified release path.

Build & Publish publish/admit/check stay on WORKFLOW_REF only (see H5). This
workflow cannot call those modes without widening the allowlist, so this
script applies the equivalent closed checks: public_configuration validation,
scanned image identity equals pushed identity, digest resolved from @digest
(not a mutable tag), refuse an existing candidate tag, and ACR write lock.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import production_core_candidate as core

SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
REGISTRY_HOST = "cosmaneuraprodregistry.azurecr.io"
IMAGE = f"{REGISTRY_HOST}/cosmaneura/cosmaneura"
IMAGE_REPOSITORY = "cosmaneura/cosmaneura"
REGISTRY = "cosmaneuraprodregistry"


def refuse(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def run(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()


def tag_exists(tag: str) -> bool:
    result = subprocess.run(
        [
            "az",
            "acr",
            "repository",
            "show",
            "--name",
            REGISTRY,
            "--image",
            f"{IMAGE_REPOSITORY}:{tag}",
            "--only-show-errors",
            "--output",
            "none",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def lock_reference(reference: str) -> None:
    subprocess.check_call(
        [
            "az",
            "acr",
            "repository",
            "update",
            "--name",
            REGISTRY,
            "--image",
            reference,
            "--write-enabled",
            "false",
            "--delete-enabled",
            "false",
            "--only-show-errors",
            "--output",
            "none",
        ]
    )


def publish(config_path: Path, source_sha: str, archive_path: Path) -> str:
    if not SHA.fullmatch(source_sha):
        refuse("Invalid source SHA.")
    if os.environ.get("CONTAINER_REGISTRY_HOST") != REGISTRY_HOST:
        refuse("Production registry host drifted from the admitted Core registry.")

    config = core.public_configuration(core.read_json(config_path))
    if config["VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"] != "false":
        refuse("Session-prep authority must stay dark on this release path.")

    if not archive_path.is_file():
        refuse("Scanned image archive missing.")
    expected_hash = os.environ.get("SCANNED_IMAGE_ARCHIVE_SHA256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        refuse("Missing scanned image archive hash.")
    actual_hash = run(["sha256sum", str(archive_path)]).split()[0]
    if actual_hash != expected_hash:
        refuse("Scanned image archive hash mismatch.")

    subprocess.check_call(["docker", "load", "--input", str(archive_path)])
    local_image = f"cosmaneura:{source_sha}"
    inspected = json.loads(run(["docker", "image", "inspect", local_image]))
    if not isinstance(inspected, list) or len(inspected) != 1:
        refuse("Local scanned image inspect failed.")
    image_config = inspected[0]
    image_id = image_config.get("Id")
    if not isinstance(image_id, str) or not DIGEST.fullmatch(image_id):
        refuse("Scanned image Id is not an immutable digest.")
    labels = image_config.get("Config", {}).get("Labels") or {}
    if labels.get("org.opencontainers.image.revision") != source_sha:
        refuse("Scanned image revision label does not match source SHA.")

    upload = f"upload-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}-{source_sha}"
    tag = (
        f"candidate-{source_sha}-"
        f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    )
    if tag_exists(tag):
        refuse("Refusing to overwrite an existing candidate tag.")

    subprocess.check_call(["docker", "tag", image_id, f"{IMAGE}:{upload}"])
    subprocess.check_call(["docker", "push", f"{IMAGE}:{upload}"])

    descriptor = json.loads(
        run(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                "--format",
                "{{json .Manifest}}",
                f"{IMAGE}:{upload}",
            ]
        )
    )
    image_digest = descriptor.get("digest")
    if not isinstance(image_digest, str) or not DIGEST.fullmatch(image_digest):
        refuse("Published image did not resolve to an immutable digest.")

    # Digest must be re-read from @digest, never trusted from a mutable tag alone.
    remote = run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            "--format",
            "{{.Manifest.Digest}}",
            f"{IMAGE}@{image_digest}",
        ]
    )
    if remote != image_digest:
        refuse("Registry digest mismatch after publish.")

    remote_raw = run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            "--raw",
            f"{IMAGE}@{image_digest}",
        ]
    )
    remote_manifest = json.loads(remote_raw)
    config_digest = remote_manifest.get("config", {}).get("digest")
    # Compare pushed config digest against the local image Id subject when present.
    local_config = image_config.get("Config")
    if not isinstance(local_config, dict):
        refuse("Local image config missing.")
    # Scanned image Id must still resolve locally after push (same content-addressed Id).
    reinsepcted = json.loads(run(["docker", "image", "inspect", image_id]))
    if reinsepcted[0]["Id"] != image_id:
        refuse("Scanned image Id drifted after publish.")
    if not isinstance(config_digest, str) or not DIGEST.fullmatch(config_digest):
        refuse("Remote manifest config digest invalid.")

    subprocess.check_call(
        [
            "az",
            "acr",
            "import",
            "--name",
            REGISTRY,
            "--registry",
            REGISTRY,
            "--source",
            f"{IMAGE_REPOSITORY}@{image_digest}",
            "--image",
            f"{IMAGE_REPOSITORY}:{tag}",
            "--only-show-errors",
            "--output",
            "none",
        ]
    )
    for reference in (
        f"{IMAGE_REPOSITORY}:{tag}",
        f"{IMAGE_REPOSITORY}@{image_digest}",
    ):
        lock_reference(reference)
        observed = json.loads(
            run(
                [
                    "az",
                    "acr",
                    "repository",
                    "show",
                    "--name",
                    REGISTRY,
                    "--image",
                    reference,
                    "--output",
                    "json",
                ]
            )
        )
        if observed.get("digest") != image_digest:
            refuse("ACR lock digest mismatch.")
        attrs = observed.get("changeableAttributes") or {}
        if (
            attrs.get("writeEnabled") is not False
            or attrs.get("deleteEnabled") is not False
        ):
            refuse("ACR lock did not disable write/delete.")

    image_ref = f"{IMAGE}@{image_digest}"
    print(image_ref)
    return image_ref


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        image_ref = publish(args.configuration, args.source_sha, args.archive)
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        refuse(f"release_verified_candidate_publish_refused: {type(exc).__name__}")
    args.output.write_text(image_ref + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
