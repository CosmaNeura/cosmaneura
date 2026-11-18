"""Rebuild the reviewed Scribe runtime locally, verify its OCI pin, layer Core.

No registry writes, runtime settings, application startup or release commands.
Scribe's source is public and fetched by immutable commit. The OCI is rebuilt
from its locked dependencies, so no published runtime image or tag is needed.
"""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from scripts.scribe_runtime_check import command, verify_runtime
from scripts.scribe_runtime_lineage import MODULE_PATH, RUNTIME_SHA256
from scripts.scribe_runtime_oci import (
    digest,
    docker_archive_to_oci,
    normalize_build_runtime,
    package,
)
from scripts.scribe_runtime_pin import (
    DOCKERFILE_FRONTEND,
    RUNTIME_DIGEST_LABEL,
    SCRIBE_RUNTIME_IMAGE_DIGEST,
    SCRIBE_SOURCE_REPOSITORY,
    SCRIBE_SOURCE_REVISION,
    UV_BUILDER_IMAGE,
)


def source_context(repository: Path, output: Path) -> None:
    if (
        command(["git", "-C", str(repository), "rev-parse", "HEAD"])
        != SCRIBE_SOURCE_REVISION
    ):
        raise ValueError("scribe_runtime_source_revision_rejected")
    names = command(
        [
            "git",
            "-C",
            str(repository),
            "ls-files",
            "--",
            "README.md",
            "pyproject.toml",
            "uv.lock",
            "services/api",
            "deployment/Dockerfile.core-bundle",
            "deployment/scribe-runtime-python",
            "scripts/standalone-provider/prepare_core_bundle_context.py",
        ]
    ).splitlines()
    if not names:
        raise ValueError("scribe_runtime_source_missing")
    # Adapt the sealed runtime subset from prepare_core_bundle_context.py. The
    # receiver identity checkpoint and migrations are outside the runtime-only
    # builder target; no receiver context, configuration or identity is invented.
    for name in names:
        source = repository / name
        expected = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repository),
                "show",
                f"{SCRIBE_SOURCE_REVISION}:{name}",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout
        if (
            source.is_symlink()
            or not source.is_file()
            or source.read_bytes() != expected
        ):
            raise ValueError("scribe_runtime_source_bytes_rejected")
        if name in {
            "deployment/Dockerfile.core-bundle",
            "deployment/scribe-runtime-python",
        }:
            target = output / source.name
        elif name.startswith("scripts/"):
            continue
        else:
            target = output / "sealed" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(expected)
        target.chmod(0o644)
    dockerfile = output / "Dockerfile.core-bundle"
    original = dockerfile.read_text()
    if not original.startswith("# syntax=docker/dockerfile:1\n"):
        raise ValueError("scribe_runtime_dockerfile_rejected")
    dockerfile.write_text(
        original.replace(
            "# syntax=docker/dockerfile:1\n",
            f"# syntax={DOCKERFILE_FRONTEND}\n",
            1,
        )
    )


def prepare_runtime(
    core_image: str, root: Path, builder: str | None = None
) -> tuple[Path, str, str, str, str, list[str]]:
    """Run the identical refused-unless-reviewed builder and package path."""
    core_archive = root / "core.tar"
    command(["docker", "image", "save", "--output", str(core_archive), core_image])
    core_digest, core = docker_archive_to_oci(core_archive, root / "core-oci")
    if core.get("architecture") != "amd64" or core.get("os") != "linux":
        raise ValueError("scribe_runtime_core_platform_rejected")
    core_config = core.get("config")
    if not isinstance(core_config, dict):
        raise ValueError("scribe_runtime_core_config_rejected")
    core_revision = (core_config.get("Labels") or {}).get(
        "org.opencontainers.image.revision", ""
    )
    if not re.fullmatch(r"[0-9a-f]{40}", core_revision):
        raise ValueError("scribe_runtime_core_revision_rejected")
    core_user = core_config.get("User") or "0:0"
    if not re.fullmatch(r"[0-9]+(?::[0-9]+)?", core_user):
        raise ValueError("scribe_runtime_core_user_rejected")
    repository = root / "source"
    command(
        [
            "git",
            "clone",
            "--quiet",
            "--no-checkout",
            SCRIBE_SOURCE_REPOSITORY,
            str(repository),
        ]
    )
    command(
        [
            "git",
            "-C",
            str(repository),
            "checkout",
            "--quiet",
            "--detach",
            SCRIBE_SOURCE_REVISION,
        ]
    )
    context = root / "context"
    context.mkdir()
    source_context(repository, context)
    core_ref = "core-substrate@" + core_digest
    core_context = f"{core_ref}=oci-layout://{root / 'core-oci'}@{core_digest}"
    base = [
        "docker",
        "buildx",
        "build",
        "--platform",
        "linux/amd64",
        "--provenance=false",
        "--sbom=false",
    ]
    if builder:
        base.extend(["--builder", builder])
    stage = root / "runtime-stage.tar"
    command(
        [
            *base,
            "--target",
            "scribe-builder",
            "--build-context",
            core_context,
            "--build-arg",
            "CORE_IMAGE=" + core_ref,
            "--build-arg",
            "UV_BUILDER_IMAGE=" + UV_BUILDER_IMAGE,
            "--build-arg",
            "CORE_SOURCE_REVISION=" + core_revision,
            "--build-arg",
            "SCRIBE_SOURCE_REVISION=" + SCRIBE_SOURCE_REVISION,
            "--build-arg",
            "CORE_RUNTIME_USER=" + core_user,
            "--output",
            "type=docker,dest=" + str(stage),
            "-f",
            str(context / "Dockerfile.core-bundle"),
            str(context),
        ]
    )
    # Export/load only an untagged local builder image. Inspect its saved
    # configuration digest; never select a registry tag or publish it.
    with tarfile.open(stage) as archive:
        source = archive.extractfile("manifest.json")
        if source is None:
            raise ValueError("scribe_runtime_build_archive_rejected")
        entries = json.load(source)
        if len(entries) != 1 or entries[0].get("RepoTags"):
            raise ValueError("scribe_runtime_build_archive_rejected")
        source = archive.extractfile(entries[0]["Config"])
        if source is None:
            raise ValueError("scribe_runtime_build_archive_rejected")
        source.read()
    loaded = command(["docker", "image", "load", "--input", str(stage)])
    identities = re.findall(r"(?m)^Loaded image ID: (sha256:[0-9a-f]{64})$", loaded)
    if len(identities) != 1:
        raise ValueError("scribe_runtime_loaded_image_ambiguous")
    image_id = identities[0]
    container = command(
        [
            "docker",
            "create",
            "--network",
            "none",
            "--entrypoint",
            "/bin/true",
            image_id,
        ]
    )
    try:
        runtime = root / "runtime"
        command(["docker", "cp", container + ":/opt/scribe/runtime", str(runtime)])
    finally:
        command(["docker", "rm", container])
    module = (
        runtime
        / "lib/python3.13/site-packages/cosmaneura_scribe"
        / Path(MODULE_PATH).name
    )
    if digest(module.read_bytes()) != "sha256:" + RUNTIME_SHA256:
        raise ValueError("scribe_runtime_built_module_rejected")
    normalize_build_runtime(runtime)
    actual = package(runtime, root / "runtime-oci")
    return runtime, actual, core_ref, core_context, core_revision, base


def derive_runtime_digest(
    core_image: str, manifest_out: Path, builder: str | None = None
) -> str:
    """Derive on the real builder path without building or tagging a Core layer."""
    with tempfile.TemporaryDirectory(prefix="core-scribe-derive-") as temporary:
        runtime, actual, *_ = prepare_runtime(core_image, Path(temporary), builder)
        entries = []
        for path in [runtime, *sorted(runtime.rglob("*"))]:
            info = path.lstat()
            entry: dict[str, object] = {
                "path": path.relative_to(runtime).as_posix(),
                "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            }
            if path.is_symlink():
                target = str(path.readlink())
                entry.update(type="symlink", size=len(target.encode()), target=target)
            elif path.is_dir():
                entry.update(type="directory", size=0)
            elif path.is_file():
                entry.update(
                    type="file", size=info.st_size, sha256=digest(path.read_bytes())
                )
            else:
                raise ValueError("scribe_runtime_manifest_entry_rejected")
            entries.append(json.dumps(entry, sort_keys=True, separators=(",", ":")))
        manifest_out.write_text("\n".join(entries) + "\n")
        return actual


def build(
    core_image: str,
    output_image: str | None,
    builder: str | None = None,
    output_oci: Path | None = None,
) -> str:
    with tempfile.TemporaryDirectory(prefix="core-scribe-build-") as temporary:
        root = Path(temporary)
        runtime, actual, core_ref, core_context, core_revision, base = prepare_runtime(
            core_image, root, builder
        )
        export = (
            ["--output", "type=oci,dest=" + str(output_oci)]
            if output_oci
            else ["--load", "--tag", output_image]
        )
        verify_runtime(runtime, actual)
        runtime_ref = "scribe-runtime@" + SCRIBE_RUNTIME_IMAGE_DIGEST
        layer = root / "layer"
        layer.mkdir()
        (layer / "Dockerfile").write_text(
            f"FROM {core_ref}\n"
            f"COPY --from={runtime_ref} /opt/scribe/runtime /opt/scribe/runtime\n"
            f'LABEL {RUNTIME_DIGEST_LABEL}="{SCRIBE_RUNTIME_IMAGE_DIGEST}" '
            f'com.cosmaneura.core-source-revision="{core_revision}" '
            f'com.cosmaneura.scribe-source-revision="{SCRIBE_SOURCE_REVISION}"\n'
        )
        command(
            [
                *base,
                "--build-context",
                core_context,
                "--build-context",
                f"{runtime_ref}=oci-layout://{root / 'runtime-oci'}@{SCRIBE_RUNTIME_IMAGE_DIGEST}",
                *export,
                str(layer),
            ]
        )
        return actual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-image", required=True)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output-image")
    output.add_argument("--output-oci", type=Path)
    output.add_argument("--derive-runtime-digest", action="store_true")
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--builder")
    args = parser.parse_args()
    if args.derive_runtime_digest != (args.manifest_out is not None):
        parser.error("--derive-runtime-digest requires --manifest-out, exclusively")
    try:
        if args.derive_runtime_digest:
            print(
                derive_runtime_digest(args.core_image, args.manifest_out, args.builder)
            )
        else:
            print(
                build(args.core_image, args.output_image, args.builder, args.output_oci)
            )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        print("scribe_runtime_build_refused")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
