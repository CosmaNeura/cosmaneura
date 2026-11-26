"""Pinned inputs and the shared production-checks receipt grammar."""

import base64
import hashlib
import json
import re
import secrets
import subprocess
import zlib
from pathlib import Path
from typing import Any

import runner

PACKAGE = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE.parents[1]
REVIEWED_SOURCE = "cf67a63129426c558b84559618822a6b59e7cdba"
PACKAGE_SHA = "6c1721ace195de69e59faffabeaa10a540f785b13f240ae128c89330c48d8ab4"
RUNNER_SHA = "b940747c7ed2fdd0d414dca664c8d642adae0f92539fe2ae28d28c72f59a9b59"
BOOTSTRAP_SHA = "b722cedc815e259920c8cd6f2cb5f891369699183e853612bc95567d83d79a67"
IMAGE = (
    "cosmaneuraprodregistry.azurecr.io/cosmaneura/cosmaneura@sha256:"
    "877fe96e9b43297be2ac479ef939c5e47476229fc867062ad0fda2bd1a50fac6"
)
PRODUCTION_JOB = "cosmaneura-prod-migrate"
SELECTORS = ("database-url", "database-url-v2")
CHECKS_WORKFLOW = "run_production_checks.yml"
G2 = (
    '{"abort_counts":{"practice_conflicts":0,"reused_uuids":0,"zero_uuids":0},'
    '"e78845cecca6_would_abort":false,"is_ancestor_of_repository_head":true,'
    '"migration_head":"49126bf99c72","pending_migrations":["ddd6261fa36f","e78845cecca6",'
    '"69bad2e072e2","b38d5a410e49","c764298c7590","5f3c9a1e7b24","5708b4399768"],'
    '"provider_practice_authority_null_practice_id_count":0,"refusal_reasons":["pending_migrations"],'
    '"repository_head":"5708b4399768","scope":"core"}'
)


class Stop(Exception):
    """Only source-defined diagnostic codes may be passed to this exception."""


def require(condition, code):
    if not condition:
        raise Stop(code)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def exact_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)


def execution_name(value, job):
    return isinstance(value, str) and re.fullmatch(
        re.escape(job) + r"-[a-z0-9]{1,40}", value
    )


def git(*args):
    return subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), *args], stderr=subprocess.DEVNULL
    )


def verify_package():
    manifest = (PACKAGE / "SHA256SUMS").read_bytes()
    require(sha256(manifest) == PACKAGE_SHA, "package_manifest_pin")
    for line in manifest.decode().splitlines():
        digest, path = line.split("  ./", 1)
        require(sha256((PACKAGE / path).read_bytes()) == digest, "package_file_pin")
    require(sha256((PACKAGE / "runner.py").read_bytes()) == RUNNER_SHA, "runner_pin")
    bootstrap = (PACKAGE / "bootstrap-args.txt").read_bytes()
    require(
        sha256(bootstrap) == BOOTSTRAP_SHA and RUNNER_SHA.encode() in bootstrap,
        "bootstrap_pin",
    )
    require(
        (PACKAGE / "candidate-access-counts.sql").read_bytes()
        == runner.ACCESS_SQL.encode(),
        "sql_pin",
    )
    runner.self_check()


def candidate_bundle(candidate):
    require(exact_sha(candidate), "candidate_sha")
    names = git("ls-tree", "-rz", "--name-only", candidate, "--", "migrations/versions")
    paths = [path.decode() for path in names.split(b"\0") if path.endswith(b".py")]
    require(
        len(paths) == 97
        and all(
            re.fullmatch(r"migrations/versions/[A-Za-z0-9_]+\.py", path)
            for path in paths
        ),
        "migration_paths",
    )
    migrations = {path: git("show", f"{candidate}:{path}").decode() for path in paths}
    require(
        runner.manifest_of(migrations) == runner.EXPECT_MIGRATIONS_MANIFEST,
        "migration_manifest",
    )
    scripts = {
        path: git("show", f"{candidate}:{path}").decode()
        for path in runner.EXPECT_SCRIPTS
    }
    for path, digest in runner.EXPECT_SCRIPTS.items():
        require(sha256(scripts[path].encode()) == digest, "script_pin")
    return scripts, migrations


def encode(data):
    return base64.b64encode(zlib.compress(data, 9)).decode("ascii")


def encoded_inputs(candidate):
    scripts, migrations = candidate_bundle(candidate)
    # Unique execution correlation carried as legal JSON whitespace. The decoded
    # mapping still contains only the two reviewed scripts and no extra key.
    nonce = "".join(
        "\t" if bit == "1" else " " for bit in f"{secrets.randbits(128):0128b}"
    )
    return {
        "CORE_RUNNER_Z": encode((PACKAGE / "runner.py").read_bytes()),
        "CORE_BUNDLE_Z": encode((nonce + json.dumps({"scripts": scripts})).encode()),
        "CORE_MIGRATIONS_Z": encode(json.dumps(migrations).encode()),
    }


def parse_markers(lines):
    """Check the whole stream, including malformed, extra and duplicate records."""
    g2_index = 4
    prefix = ["RUNNER_SHA_OK=1", "RUN_START=1", "BUNDLE_VERIFIED=97"]
    shape = "URL_SHAPE=" + ",".join(f"{key}:[01]" for key in runner.SHAPE_KEYS)
    require(
        type(lines) is list
        and len(lines) == 18
        and all(type(line) is str for line in lines),
        "incomplete_or_extra_log",
    )
    require(lines[:3] == prefix and re.fullmatch(shape, lines[3]), "invalid_log_marker")
    require(lines[g2_index].startswith("PRECHECK_JSON="), "invalid_log_marker")
    require(
        lines[g2_index + 1 : 7] == ["PRECHECK_EXIT=1", "ACCESS_LOCK_TIMEOUT_MS=4000"]
        and lines[-2:] == ["ACCESS_ROWS=9", "RUN_DONE=1"],
        "invalid_log_marker",
    )
    counts = {}
    for label, line in zip(runner.ACCESS_LABELS, lines[7:-2], strict=True):
        match = re.fullmatch(r"ACCESS_COUNT=" + label + r",([0-9]{1,19})", line)
        if match is None:
            raise Stop("invalid_access_marker")
        count = int(match[1])
        require(count <= 9223372036854775807, "invalid_access_marker")
        counts[label] = count
    shape_match = "match" if lines[g2_index] == f"PRECHECK_JSON={G2}" else "differs"
    return counts, shape_match


def receipt_header(candidate, run_id, execution, selector) -> dict[str, Any]:
    return {
        "version": 1,
        "source": "production",
        "candidate_sha": candidate,
        "workflow_sha": candidate,
        "reviewed_source_sha": REVIEWED_SOURCE,
        "migration_manifest": runner.EXPECT_MIGRATIONS_MANIFEST,
        "package_sha256": PACKAGE_SHA,
        "run_id": run_id,
        "run_attempt": 1,
        "execution": execution,
        "selector": selector,
        "image": IMAGE,
        "runner_sha256": RUNNER_SHA,
        "status": "Succeeded",
    }


def verify_receipt(receipt, candidate, run_id):
    execution, selector = receipt.get("execution"), receipt.get("selector")
    require(
        execution_name(execution, PRODUCTION_JOB) and selector in SELECTORS,
        "receipt_identity",
    )
    expected = receipt_header(candidate, run_id, execution, selector)
    counts = receipt.get("access_counts")
    require(
        type(counts) is dict
        and tuple(counts) == runner.ACCESS_LABELS
        and all(
            type(value) is int and 0 <= value <= 9223372036854775807
            for value in counts.values()
        ),
        "receipt_counts",
    )
    template_digest = receipt.get("template_sha256")
    timeout = receipt.get("replica_timeout_seconds")
    require(
        isinstance(template_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", template_digest)
        and type(timeout) is int
        and timeout > 0,
        "receipt_template",
    )
    expected.update(
        template_unchanged=True,
        template_sha256=template_digest,
        retry_limit=0,
        replica_timeout_seconds=timeout,
        execution_count=1,
        g2_shape="match",
        access_counts=counts,
    )
    # Canonical serialization also distinguishes booleans from integers.
    require(json.dumps(receipt) == json.dumps(expected), "receipt_contract")
