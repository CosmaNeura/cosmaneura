#!/usr/bin/env python3
"""Core-only immutable producer/promotion admission for the canonical workflow.

GitHub's authenticated run/artifact association and archive digest establish
the producer, not a caller's self-hashed JSON. This is not provider activation.
No application imports, database access, image builds or container starts.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from superadmin_capability_config_preflight import (
    _clerk_issuer_from_publishable_key,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "CosmaNeura/cosmaneura"
WORKFLOW = ".github/workflows/build_and_publish_local.yml"
WORKFLOW_REF = f"{REPOSITORY}/{WORKFLOW}@refs/heads/master"
REGISTRY = "cosmaneuraprodregistry"
IMAGE = f"{REGISTRY}.azurecr.io/cosmaneura/cosmaneura"
IMAGE_REPOSITORY = "cosmaneura/cosmaneura"
PUBLIC_KEYS = frozenset(
    "NODE_ENV OPENAPI_JSON_PATH VITE_CLERK_PUBLIC_KEY VITE_INTAKE_CHAT_DOMAIN "
    "VITE_POSTHOG_HOST VITE_POSTHOG_KEY VITE_PYTHON_URL VITE_SENTRY_DSN "
    "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED VITE_SOVEREIGN_PROVIDER_URL".split()
)
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_JSON = 128 * 1024
MAX_METADATA = 1024 * 1024
MAX_ARCHIVE = 1024 * 1024
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}


class CandidateRefused(ValueError):
    """Closed, value-free failure; provider/credential values are never logged."""


def require(condition: Any) -> None:
    if not condition:
        raise CandidateRefused("production_core_candidate_refused")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def decode(raw: bytes, *, maximum: int = MAX_JSON) -> Any:
    require(0 < len(raw) <= maximum)

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    def number(_value: str) -> None:
        raise CandidateRefused("production_core_candidate_refused")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=number)
    except (ValueError, UnicodeError, RecursionError):
        raise CandidateRefused("production_core_candidate_refused") from None


def object_fields(value: Any, keys: set[str]) -> dict[str, Any]:
    require(type(value) is dict and set(value) == keys)
    return value


def positive(value: Any) -> int:
    require(type(value) is int and 0 < value < 10**20)
    return value


def read_json(path: Path) -> Any:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode))
        return decode(stream.read(MAX_JSON + 1))


def command(args: list[str], *, github: bool = False) -> bytes:
    # Existing normal Docker/Azure caches are preserved; upstream build and
    # 1Password capabilities never reach these publication/read-only clients.
    allowed = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL")
    if args[0] in {"docker", "az"}:
        allowed += ("DOCKER_CONFIG", "AZURE_CONFIG_DIR", "AZURE_EXTENSION_DIR")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    if github:
        require(bool(os.environ.get("GH_TOKEN")))
        environment["GH_TOKEN"] = os.environ["GH_TOKEN"]
    environment.update(
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_NO_LAZY_FETCH="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
    )
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            args,
            stdout=output,
            stderr=subprocess.DEVNULL,
            env=environment,
            check=False,
            timeout=60,
        )
        require(result.returncode == 0)
        output.seek(0)
        raw = output.read(MAX_ARCHIVE + 1)
        require(len(raw) <= MAX_ARCHIVE)
        return raw


def api(path: str) -> Any:
    require(path.startswith(f"repos/{REPOSITORY}/") and ".." not in path)
    raw = command(["gh", "api", "--hostname", "github.com", path], github=True)
    return decode(raw, maximum=MAX_METADATA)


def source_context() -> dict[str, Any]:
    expected = {
        "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/master",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
    }
    require(all(os.environ.get(key) == value for key, value in expected.items()))
    sha = os.environ.get("GITHUB_SHA", "")
    require(SHA.fullmatch(sha))
    git = ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(ROOT)]
    head = command([*git, "rev-parse", "--verify", "HEAD^{commit}"]).decode().strip()
    tree = command([*git, "rev-parse", "--verify", "HEAD^{tree}"]).decode().strip()
    require(head == sha and SHA.fullmatch(tree))
    command([*git, "diff", "--quiet", "HEAD", "--"])
    require(not command([*git, "ls-files", "--others", "--exclude-standard"]).strip())
    require(
        api("repos/" + REPOSITORY + "/git/ref/heads/master")["object"]["sha"] == sha
    )
    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run))
    require(re.fullmatch(r"[1-9][0-9]{0,9}", attempt))
    return {
        "source_sha": sha,
        "source_tree": tree,
        "run_id": int(run),
        "run_attempt": int(attempt),
    }


def public_configuration(value: Any) -> dict[str, str]:
    config = object_fields(value, set(PUBLIC_KEYS))
    require(
        all(
            type(item) is str and 0 < len(item) <= 8192 and "\x00" not in item
            for item in config.values()
        )
    )
    require(
        config["NODE_ENV"] == "production"
        and config["OPENAPI_JSON_PATH"] == "openapi.json"
    )
    require(config["VITE_PYTHON_URL"] == "https://app.cosmaneura.com/")
    require(config["VITE_INTAKE_CHAT_DOMAIN"] == "chat.cosmaneura.com")
    require(config["VITE_SOVEREIGN_PROVIDER_URL"] == "https://sovereign.cosmaneura.com")
    # Preserve current master's dark build admission. PR896 is separately
    # reviewed and must be composed explicitly before a true flag is admitted.
    require(config["VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"] == "false")
    issuer = _clerk_issuer_from_publishable_key(
        config["VITE_CLERK_PUBLIC_KEY"], environment="production"
    )
    require(re.fullmatch(r"https://[a-z0-9]+(?:[.-][a-z0-9]+)+", issuer))
    require("staging" not in issuer and not issuer.endswith(".accounts.dev"))
    return config


def asset_tree(path: Path) -> str:
    require(path.is_dir() and not path.is_symlink())
    rows = []
    for item in sorted(path.rglob("*")):
        mode = item.lstat().st_mode
        require(stat.S_ISDIR(mode) or stat.S_ISREG(mode))
        if stat.S_ISREG(mode):
            require(item.stat().st_size <= 32 * 1024 * 1024)
            rows.append([item.relative_to(path).as_posix(), digest(item.read_bytes())])
    require(rows and len(rows) <= 10000)
    return digest(canonical(rows))


def panel_identity(assets: Path) -> dict[str, str]:
    source = (ROOT / "web/app/sovereign/SovereignPanelAsset.ts").read_text()
    values = {}
    for name, pattern in (
        ("SHA256", "[0-9a-f]{64}"),
        ("UPSTREAM_COMMIT", "[0-9a-f]{40}"),
        ("INTEGRITY", "sha256-[A-Za-z0-9+/=]+"),
    ):
        matches = re.findall(
            r"export const SOVEREIGN_PANEL_" + name + r'\s*=\s*"(' + pattern + r')"',
            source,
        )
        require(len(matches) == 1)
        values[name] = matches[0]
    raw = (assets / f"vendor/cosmaneura-panel.v1.{values['SHA256']}.js").read_bytes()
    require(hashlib.sha256(raw).hexdigest() == values["SHA256"])
    require(
        "sha256-" + base64.b64encode(hashlib.sha256(raw).digest()).decode()
        == values["INTEGRITY"]
    )
    return {
        "sha256": values["SHA256"],
        "upstream_commit": values["UPSTREAM_COMMIT"],
        "integrity": values["INTEGRITY"],
    }


def manifest_identity(
    raw: bytes, expected: str, image_config: dict[str, Any] | None = None
) -> None:
    require(type(expected) is str and DIGEST.fullmatch(expected))
    # Buildx prints a final LF after its raw document. Accept only the exact
    # digest-matching bytes, never arbitrary whitespace or reserialized JSON.
    if digest(raw) != expected and raw.endswith(b"\n"):
        raw = raw[:-1]
    require(digest(raw) == expected)
    manifest = decode(raw)
    require(
        manifest.get("schemaVersion") == 2
        and manifest.get("mediaType") in MANIFEST_TYPES
    )
    config = manifest.get("config")
    require(type(config) is dict and DIGEST.fullmatch(config.get("digest", "")))
    require(type(manifest.get("layers")) is list and manifest["layers"])
    if image_config is not None:
        require(config["digest"] == image_config["Id"])


def image_source(context: dict[str, Any], image_config: dict[str, Any]) -> None:
    require(
        image_config.get("Os") == "linux"
        and image_config.get("Architecture") == "amd64"
    )
    require(
        type(image_config.get("Id")) is str and DIGEST.fullmatch(image_config["Id"])
    )
    require(
        image_config["Config"]["Labels"]["org.opencontainers.image.revision"]
        == context["source_sha"]
    )
    env = image_config["Config"].get("Env")
    require(type(env) is list and env.count("PYTHON_ENV=production") == 1)
    require(sum(item.startswith("PYTHON_ENV=") for item in env) == 1)


def compiled_assets(assets: Path, image_assets: Path) -> tuple[str, dict[str, str]]:
    assets_digest = asset_tree(assets)
    require(asset_tree(image_assets) == assets_digest)
    return assets_digest, panel_identity(image_assets)


def candidate_record(
    context: dict[str, Any],
    config: Any,
    manifest: bytes,
    image_config: dict[str, Any],
    assets_digest: str,
    panel: dict[str, str],
    image_digest: str,
) -> dict[str, Any]:
    config = public_configuration(config)
    manifest_identity(manifest, image_digest, image_config)
    image_source(context, image_config)
    record = {
        "schema_version": 1,
        "purpose": "verified-production-core-candidate",
        "repository": REPOSITORY,
        "workflow_ref": WORKFLOW_REF,
        **context,
        "image": IMAGE + "@" + image_digest,
        "image_config_digest": image_config["Id"],
        "public_configuration_digest": digest(canonical(config)),
        "frontend_assets_digest": assets_digest,
        "panel": panel,
        "frontend_authority_enabled": False,
    }
    return validate_record(record, context, context["run_id"], context["run_attempt"])


def validate_record(
    record: Any, context: dict[str, Any], run_id: int, attempt: int
) -> dict[str, Any]:
    record = object_fields(
        record,
        set(
            "schema_version purpose repository workflow_ref source_sha source_tree run_id run_attempt image image_config_digest public_configuration_digest frontend_assets_digest panel frontend_authority_enabled".split()
        ),
    )
    require(type(record["schema_version"]) is int and record["schema_version"] == 1)
    require(record["purpose"] == "verified-production-core-candidate")
    require(
        record["repository"] == REPOSITORY and record["workflow_ref"] == WORKFLOW_REF
    )
    require(
        record["source_sha"] == context["source_sha"]
        and record["source_tree"] == context["source_tree"]
    )
    require(
        positive(record["run_id"]) == run_id
        and positive(record["run_attempt"]) == attempt
    )
    require(
        type(record["image"]) is str
        and re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", record["image"])
    )
    for key in (
        "image_config_digest",
        "public_configuration_digest",
        "frontend_assets_digest",
    ):
        require(type(record[key]) is str and DIGEST.fullmatch(record[key]))
    panel = object_fields(record["panel"], {"sha256", "upstream_commit", "integrity"})
    require(
        type(panel["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", panel["sha256"])
    )
    require(
        type(panel["upstream_commit"]) is str
        and SHA.fullmatch(panel["upstream_commit"])
    )
    require(
        panel["integrity"]
        == "sha256-" + base64.b64encode(bytes.fromhex(panel["sha256"])).decode()
    )
    require(record["frontend_authority_enabled"] is False)
    return record


def required_jobs() -> set[str]:
    source = (ROOT / WORKFLOW).read_text()
    shards = re.findall(r"^          - shard: ([a-z-]+)$", source, re.MULTILINE)
    require(shards and len(shards) == len(set(shards)))
    return {
        "Authorize production promotion",
        "Release scope",
        "Backend quality",
        "Backend",
        "Frontend",
        "Production release gates",
        "Prepare production candidate",
        *("Backend tests (" + shard + ")" for shard in shards),
    }


def archive_record(raw: bytes, expected_digest: str) -> Any:
    require(0 < len(raw) <= MAX_ARCHIVE and digest(raw) == expected_digest)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1)
        item = entries[0]
        require(item.filename == "candidate.json" and not item.is_dir())
        require(not item.flag_bits & 1 and item.file_size <= MAX_JSON)
        require(not stat.S_ISLNK(item.external_attr >> 16))
        with archive.open(item) as stream:
            return decode(stream.read(MAX_JSON + 1))


def verify_evidence(
    context: dict[str, Any],
    run_id: int,
    attempt: int,
    artifact_id: int,
    run: Any,
    workflow: Any,
    jobs: Any,
    artifact: Any,
    archive: bytes,
) -> dict[str, Any]:
    require(run_id != context["run_id"])
    require(run["id"] == run_id and run["run_attempt"] == attempt)
    require(run["status"] == "completed" and run["conclusion"] == "success")
    require(run["event"] == "workflow_dispatch" and run["head_branch"] == "master")
    require(
        run["repository"]["full_name"] == REPOSITORY
        and run["head_sha"] == context["source_sha"]
    )
    require(
        run["actor"]["login"] == "JohnClem"
        and run["triggering_actor"]["login"] == "JohnClem"
    )
    require(workflow["path"] == WORKFLOW and workflow["state"] == "active")
    require(run["path"] == WORKFLOW and run["workflow_id"] == workflow["id"])
    expected = required_jobs()
    require(
        jobs["total_count"] == len(expected) + 1
        and len(jobs["jobs"]) == jobs["total_count"]
    )
    observed = jobs["jobs"]
    require(len({item["id"] for item in observed}) == len(observed))
    require({item["name"] for item in observed} == expected | {"🚀 Deploy"})
    require(
        all(
            item["head_sha"] == context["source_sha"]
            and item["run_id"] == run_id
            and item["run_attempt"] == attempt
            and item["status"] == "completed"
            and item["conclusion"]
            == ("skipped" if item["name"] == "🚀 Deploy" else "success")
            for item in observed
        )
    )
    require(artifact["id"] == artifact_id and artifact["expired"] is False)
    require(
        artifact["name"]
        == f"production-core-candidate-{run_id}-{attempt}-{context['source_sha']}"
    )
    require(
        type(artifact["size_in_bytes"]) is int
        and 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE
    )
    require(
        type(artifact.get("digest")) is str and DIGEST.fullmatch(artifact["digest"])
    )
    association = artifact["workflow_run"]
    require(
        association["id"] == run_id
        and association["head_sha"] == context["source_sha"]
        and association["head_branch"] == "master"
    )
    require(
        association["repository_id"] == run["repository"]["id"]
        and association["head_repository_id"] == run["repository"]["id"]
    )
    return validate_record(
        archive_record(archive, artifact["digest"]), context, run_id, attempt
    )


def admit(run_id: int, attempt: int, artifact_id: int) -> dict[str, Any]:
    context = source_context()
    prefix = f"repos/{REPOSITORY}/actions"
    run = api(f"{prefix}/runs/{run_id}")
    workflow = api(f"{prefix}/workflows/build_and_publish_local.yml")
    jobs = api(f"{prefix}/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    artifact = api(f"{prefix}/artifacts/{artifact_id}")
    # Fail metadata before fetching an untrusted/oversized archive.
    require(
        artifact.get("expired") is False
        and type(artifact.get("size_in_bytes")) is int
        and 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE
    )
    raw = command(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            f"{prefix}/artifacts/{artifact_id}/zip",
        ],
        github=True,
    )
    return verify_evidence(
        context, run_id, attempt, artifact_id, run, workflow, jobs, artifact, raw
    )


def retention(tag: str, image_digest: str) -> None:
    for reference in (
        IMAGE_REPOSITORY + ":" + tag,
        IMAGE_REPOSITORY + "@" + image_digest,
    ):
        observed = decode(
            command(
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
        require(observed["digest"] == image_digest)
        require(
            observed["changeableAttributes"]
            == {
                "writeEnabled": False,
                "deleteEnabled": False,
                "readEnabled": True,
                "listEnabled": True,
            }
        )


def publish(config_path: Path, output: Path) -> None:
    context = source_context()
    config = public_configuration(read_json(config_path))
    local_image = "cosmaneura:" + context["source_sha"]
    inspected = decode(command(["docker", "image", "inspect", local_image]))
    require(type(inspected) is list and len(inspected) == 1)
    image_source(context, inspected[0])
    # Verify compiled files inside this exact local image before publication.
    # Creating/copying a stopped container never executes the application.
    container = (
        command(["docker", "create", "--network", "none", local_image]).decode().strip()
    )
    require(re.fullmatch(r"[0-9a-f]{64}", container))
    try:
        with tempfile.TemporaryDirectory() as temporary:
            image_assets = Path(temporary) / "public"
            command(["docker", "cp", container + ":/app/public", str(image_assets)])
            assets_digest, panel = compiled_assets(ROOT / "public", image_assets)
    finally:
        command(["docker", "rm", container])
    require(source_context() == context)
    upload = (
        f"upload-{context['run_id']}-{context['run_attempt']}-{context['source_sha']}"
    )
    tag = f"candidate-{context['source_sha']}-{context['run_id']}-{context['run_attempt']}"
    command(["docker", "tag", local_image, IMAGE + ":" + upload])
    command(["docker", "push", IMAGE + ":" + upload])
    descriptor = decode(
        command(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                "--format",
                "{{json .Manifest}}",
                IMAGE + ":" + upload,
            ]
        )
    )
    image_digest = descriptor["digest"]
    require(type(image_digest) is str and DIGEST.fullmatch(image_digest))
    manifest = command(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            "--raw",
            IMAGE + "@" + image_digest,
        ]
    )
    manifest_identity(manifest, image_digest, inspected[0])
    # No-force import creates the selected tag without overwriting an earlier
    # candidate. Lock both tag and manifest, then read back both before success.
    command(
        [
            "az",
            "acr",
            "import",
            "--name",
            REGISTRY,
            "--registry",
            REGISTRY,
            "--source",
            IMAGE_REPOSITORY + "@" + image_digest,
            "--image",
            IMAGE_REPOSITORY + ":" + tag,
            "--only-show-errors",
            "--output",
            "none",
        ]
    )
    for reference in (
        IMAGE_REPOSITORY + ":" + tag,
        IMAGE_REPOSITORY + "@" + image_digest,
    ):
        command(
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
    retention(tag, image_digest)
    record = candidate_record(
        context, config, manifest, inspected[0], assets_digest, panel, image_digest
    )
    require(source_context() == context)
    descriptor_file = os.open(
        output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor_file, "wb") as stream:
        stream.write(canonical(record) + b"\n")


def promotion_check(record: dict[str, Any], config: Any) -> str:
    config = public_configuration(config)
    require(digest(canonical(config)) == record["public_configuration_digest"])
    require(record["panel"] == panel_identity(ROOT / "web/public"))
    image_digest = record["image"].split("@")[1]
    raw = command(
        ["docker", "buildx", "imagetools", "inspect", "--raw", record["image"]]
    )
    manifest_identity(raw, image_digest)
    require(decode(raw)["config"]["digest"] == record["image_config_digest"])
    tag = f"candidate-{record['source_sha']}-{record['run_id']}-{record['run_attempt']}"
    retention(tag, image_digest)
    source_context()
    return record["image"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("context", "publish", "admit", "check"))
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--producer-run-id", type=int)
    parser.add_argument("--producer-attempt", type=int)
    parser.add_argument("--artifact-id", type=int)
    args = parser.parse_args()
    try:
        if args.mode == "context":
            source_context()
        elif args.mode == "publish":
            require(args.configuration is not None and args.output is not None)
            publish(args.configuration, args.output)
        else:
            run_id, attempt, artifact_id = (
                positive(value)
                for value in (
                    args.producer_run_id,
                    args.producer_attempt,
                    args.artifact_id,
                )
            )
            record = admit(run_id, attempt, artifact_id)
            if args.mode == "check":
                require(args.configuration is not None)
                print(promotion_check(record, read_json(args.configuration)))
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
        RuntimeError,
    ):
        print("production_core_candidate_refused", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
