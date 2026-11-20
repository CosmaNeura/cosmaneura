# fmt: off
from __future__ import annotations  # noqa: I001
import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NoReturn
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import production_core_candidate as core  # noqa: E402,I001
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
SCHEMA = re.compile(r"[0-9a-f]{12}\Z")
IMAGE_REF = re.compile(
    r"^cosmaneuraprodregistry\.azurecr\.io/cosmaneura/cosmaneura@sha256:[0-9a-f]{64}$"
)
REGISTRY, HOST = "cosmaneuraprodregistry", "cosmaneuraprodregistry.azurecr.io"
IMAGE, REPO = f"{HOST}/cosmaneura/cosmaneura", "cosmaneura/cosmaneura"
APPS = ("cosmaneura-prod-app", "cosmaneura-prod-worker", "cosmaneura-prod-scheduler")
RG, HEALTH = "cosmaneura-prod", "https://app.cosmaneura.com/healthcheck"
POLICY = "Image rollback restores prior digests/revisions only; Alembic is forward-only."
REVISION_LABEL = "org.opencontainers.image.revision"
def refuse(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)
def run(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()
def az_json(args: list[str]) -> Any:
    return json.loads(run(["az", *args, "--output", "json"]))
def schema_head(sha: str) -> str:
    if not SHA.fullmatch(sha):
        refuse("Invalid SHA for schema-head lookup.")
    from verify_staging_candidate import (  # noqa: E402,I001
        CandidateVerificationError,
        _candidate_schema_head,
    )
    try:
        head = _candidate_schema_head(sha, stdout=run)
    except CandidateVerificationError as exc:
        refuse(f"Cannot resolve Alembic head at {sha}: {exc}")
    if not isinstance(head, str) or not SCHEMA.fullmatch(head):
        refuse(f"Alembic head at {sha} is not a single 12-hex revision.")
    return head
def app_image(name: str) -> str:
    props = az_json(["containerapp", "show", "-n", name, "-g", RG])["properties"]
    containers = (props.get("template") or {}).get("containers") or []
    if not containers:
        refuse(f"No containers on {name}.")
    image = containers[0].get("image")
    if not isinstance(image, str) or not image:
        refuse(f"Missing image on {name}.")
    return image
def image_source_revision(image: str) -> str:
    subprocess.check_call(["docker", "pull", "--quiet", image])
    inspected = json.loads(run(["docker", "image", "inspect", image]))
    if not isinstance(inspected, list) or not inspected:
        refuse(f"Inspect failed for {image}.")
    rev = ((inspected[0].get("Config") or {}).get("Labels") or {}).get(REVISION_LABEL, "")
    if not isinstance(rev, str) or not SHA.fullmatch(rev):
        refuse(f"Image missing {REVISION_LABEL}: {image}")
    return rev
def running_source_sha() -> str:
    found: set[str] = set()
    for name in APPS:
        found.add(image_source_revision(app_image(name)))
    if len(found) != 1:
        refuse("Production apps disagree on source revision labels.")
    return found.pop()
def schema_evidence(running_sha: str, candidate_sha: str) -> dict[str, Any]:
    running_head = schema_head(running_sha)
    candidate_head = schema_head(candidate_sha)
    return {
        "running_source_sha": running_sha,
        "candidate_sha": candidate_sha,
        "schema_head_running": running_head,
        "schema_head_candidate": candidate_head,
        "migration_in_release": running_head != candidate_head,
    }
def admit_schema_before_publish(
    candidate_sha: str, *, running_sha: str | None = None
) -> dict[str, Any]:
    """Refuse on unreadable heads before any registry tag/lock."""
    if not SHA.fullmatch(candidate_sha):
        refuse("Invalid candidate SHA.")
    resolved = running_sha or running_source_sha()
    return schema_evidence(resolved, candidate_sha)
def publish(
    config_path: Path,
    source_sha: str,
    archive: Path,
    *,
    running_sha: str | None = None,
    schema_output: Path | None = None,
) -> str:
    # Schema-head refusal happens before push/import/lock so no locked tag remains.
    evidence = admit_schema_before_publish(source_sha, running_sha=running_sha)
    if schema_output is not None:
        schema_output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    if os.environ.get("CONTAINER_REGISTRY_HOST") != HOST:
        refuse("Production registry host drifted.")
    config = core.public_configuration(core.read_json(config_path))
    if config["VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"] != "false":
        refuse("Session-prep authority must stay dark.")
    expected = os.environ.get("SCANNED_IMAGE_ARCHIVE_SHA256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        refuse("Missing scanned image archive hash.")
    if run(["sha256sum", str(archive)]).split()[0] != expected:
        refuse("Scanned image archive hash mismatch.")
    subprocess.check_call(["docker", "load", "--input", str(archive)])
    inspected = json.loads(run(["docker", "image", "inspect", f"cosmaneura:{source_sha}"]))
    if not isinstance(inspected, list) or len(inspected) != 1:
        refuse("Local scanned image inspect failed.")
    image_id = inspected[0].get("Id")
    if not isinstance(image_id, str) or not DIGEST.fullmatch(image_id):
        refuse("Scanned image Id is not an immutable digest.")
    labels = (inspected[0].get("Config") or {}).get("Labels") or {}
    if labels.get(REVISION_LABEL) != source_sha:
        refuse("Scanned image revision label mismatch.")
    upload = f"upload-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}-{source_sha}"
    tag = f"candidate-{source_sha}-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    exists = subprocess.run(
        ["az", "acr", "repository", "show", "--name", REGISTRY, "--image", f"{REPO}:{tag}",
         "--only-show-errors", "--output", "none"], capture_output=True)
    if exists.returncode == 0:
        refuse("Refusing to overwrite an existing candidate tag.")
    subprocess.check_call(["docker", "tag", image_id, f"{IMAGE}:{upload}"])
    subprocess.check_call(["docker", "push", f"{IMAGE}:{upload}"])
    descriptor = json.loads(run(
        ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .Manifest}}", f"{IMAGE}:{upload}"]))
    digest = descriptor.get("digest")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        refuse("Published image missing immutable digest.")
    remote = run(["docker", "buildx", "imagetools", "inspect", "--format", "{{.Manifest.Digest}}", f"{IMAGE}@{digest}"])
    if remote != digest:
        refuse("Registry digest mismatch after publish.")
    if json.loads(run(["docker", "image", "inspect", image_id]))[0]["Id"] != image_id:
        refuse("Scanned image Id drifted after publish.")
    subprocess.check_call(
        ["az", "acr", "import", "--name", REGISTRY, "--registry", REGISTRY, "--source", f"{REPO}@{digest}",
         "--image", f"{REPO}:{tag}", "--only-show-errors", "--output", "none"])
    for reference in (f"{REPO}:{tag}", f"{REPO}@{digest}"):
        subprocess.check_call(
            ["az", "acr", "repository", "update", "--name", REGISTRY, "--image", reference,
             "--write-enabled", "false", "--delete-enabled", "false", "--only-show-errors", "--output", "none"])
        observed = az_json(["acr", "repository", "show", "--name", REGISTRY, "--image", reference])
        if observed.get("digest") != digest:
            refuse("ACR lock digest mismatch.")
        attrs = observed.get("changeableAttributes") or {}
        if attrs.get("writeEnabled") is not False or attrs.get("deleteEnabled") is not False:
            refuse("ACR lock did not disable write/delete.")
    return f"{IMAGE}@{digest}"
def record_baseline(
    path: Path, candidate_sha: str, *, running_sha: str | None = None
) -> None:
    if not SHA.fullmatch(candidate_sha):
        refuse("Invalid candidate SHA for baseline.")
    resolved = running_sha or running_source_sha()
    schema = schema_evidence(resolved, candidate_sha)
    apps = {}
    for name in APPS:
        props = az_json(["containerapp", "show", "-n", name, "-g", RG])["properties"]
        containers = (props.get("template") or {}).get("containers") or []
        if not containers:
            refuse(f"No containers on {name}.")
        apps[name] = {
            "image": containers[0].get("image"),
            "latestReadyRevisionName": props.get("latestReadyRevisionName"),
            "latestRevisionName": props.get("latestRevisionName"),
        }
    payload = {
        "resource_group": RG,
        "apps": apps,
        "migration_policy": POLICY,
        "healthcheck_url": HEALTH,
        **schema,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
def rollback(baseline_path: Path) -> None:
    baseline = json.loads(baseline_path.read_text())
    running_head = baseline.get("schema_head_running")
    candidate_head = baseline.get("schema_head_candidate")
    if (
        not isinstance(running_head, str)
        or not SCHEMA.fullmatch(running_head)
        or not isinstance(candidate_head, str)
        or not SCHEMA.fullmatch(candidate_head)
    ):
        refuse("Baseline missing code-derived schema heads.")
    if running_head != candidate_head or baseline.get("migration_in_release") is True:
        refuse(f"Refusing image rollback: migration in this release ({running_head} -> {candidate_head}); Alembic forward-only; human intervention required.")
    print("ROLLBACK: restoring recorded digests/revisions.", file=sys.stderr)
    print(POLICY, file=sys.stderr)
    failures: list[str] = []
    for name, state in baseline.get("apps", {}).items():
        image = state.get("image")
        if not image:
            failures.append(f"{name}: baseline missing image")
            continue
        try:
            live = app_image(name)
        except SystemExit:
            failures.append(f"{name}: could not read live image")
            continue
        if live == image:
            continue
        print(f"az containerapp update -n {name} -g {RG} -i {image}")
        try:
            subprocess.check_call(
                ["az", "containerapp", "update", "-n", name, "-g", RG, "-i", image, "--only-show-errors"])
        except (subprocess.SubprocessError, OSError) as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        refuse("Rollback failed for one or more apps:\n" + "\n".join(failures))
def verify_ready(expected_image: str, baseline_path: Path, evidence_path: Path) -> None:
    if not IMAGE_REF.fullmatch(expected_image):
        refuse("Expected image must be an immutable production digest ref.")
    baseline = json.loads(baseline_path.read_text())
    evidence: dict[str, Any] = {
        "expected_image": expected_image,
        "healthcheck_url": HEALTH,
        "migration_policy": POLICY,
        "baseline": baseline,
        "apps": {},
    }
    def fail(message: str) -> None:
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps(evidence, indent=2, sort_keys=True))
        rollback(baseline_path)
        refuse(message)
    try:
        status: int | None = None
        body = b""
        try:
            with urllib.request.urlopen(HEALTH, timeout=30) as response:
                status = getattr(response, "status", None) or response.getcode()
                body = response.read(256)
        except (
            urllib.error.URLError,
            TimeoutError,
            http.client.RemoteDisconnected,
            http.client.BadStatusLine,
            ConnectionError,
            OSError,
        ) as exc:
            evidence["healthcheck"] = {"result": "failed", "error": type(exc).__name__}
            fail("app.cosmaneura.com/healthcheck failed.")
        evidence["healthcheck"] = {
            "result": "passed" if status == 200 else "failed",
            "http_status": status,
            "body_prefix_len": len(body),
        }
        if status != 200:
            fail("Healthcheck returned a non-200 status.")
        for name in APPS:
            props = az_json(["containerapp", "show", "-n", name, "-g", RG])["properties"]
            ready = props.get("latestReadyRevisionName")
            if not ready:
                evidence["apps"][name] = {"error": "missing_ready_revision"}
                fail(f"{name} has no ready revision.")
            revision = az_json(
                ["containerapp", "revision", "show", "-n", name, "-g", RG, "--revision", ready])
            containers = (
                ((revision.get("properties") or {}).get("template") or {})
                .get("containers")
                or []
            )
            if not containers:
                evidence["apps"][name] = {"error": "empty_containers", "latestReadyRevisionName": ready}
                fail(f"{name} ready revision has no containers.")
            image = containers[0].get("image")
            evidence["apps"][name] = {"latestReadyRevisionName": ready, "image": image}
            if image != expected_image:
                fail(f"{name} ready revision digest mismatch.")
        evidence["result"] = "passed"
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps(evidence, indent=2, sort_keys=True))
        print(POLICY)
    except SystemExit:
        raise
    except Exception as exc:
        evidence["error"] = {"type": type(exc).__name__, "message": str(exc)}
        fail(f"verify-ready aborted: {type(exc).__name__}")
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("publish", "record-baseline", "verify-ready", "rollback", "admit-schema"),
    )
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--running-source-sha")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--schema-output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--expected-image")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "admit-schema":
            sha = args.candidate_sha or args.source_sha
            if not sha:
                refuse("admit-schema requires --candidate-sha")
            evidence = admit_schema_before_publish(sha, running_sha=args.running_source_sha)
            if args.output:
                args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        elif args.mode == "publish":
            if not args.configuration or not args.source_sha or not args.archive or not args.output:
                refuse("publish requires --configuration --source-sha --archive --output")
            image = publish(
                args.configuration,
                args.source_sha,
                args.archive,
                running_sha=args.running_source_sha,
                schema_output=args.schema_output,
            )
            args.output.write_text(image + "\n")
            print(image)
        elif args.mode == "record-baseline":
            sha = args.candidate_sha or args.source_sha
            if not args.baseline or not sha:
                refuse("record-baseline requires --baseline and --candidate-sha")
            record_baseline(args.baseline, sha, running_sha=args.running_source_sha)
        elif args.mode == "rollback":
            if not args.baseline:
                refuse("rollback requires --baseline")
            rollback(args.baseline)
        else:
            if not args.baseline or not args.expected_image or not args.evidence:
                refuse("verify-ready requires --baseline --expected-image --evidence")
            verify_ready(args.expected_image, args.baseline, args.evidence)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        refuse(f"release_verified_candidate_ops_refused: {type(exc).__name__}")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
