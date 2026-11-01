#!/usr/bin/env python3
"""Authenticated sampled Core/frontend binding, never beta qualification.

The canonical promote job records only after its existing deployment succeeds.
The reader authenticates that completed job and the original prepare artifact,
then repeats the fixed read-only runtime observations. No application imports,
secret-value requests, builds, image publication or deployment commands.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import io
import os
import re
import signal
import ssl
import stat
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import production_core_candidate as candidate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = "scripts/production_core_runtime_binding.py"
SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
TENANT = "d51754d0-bba5-4810-820e-afac542844d6"
GROUP = "cosmaneura-prod"
PREFIX = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.App"
ENVIRONMENT = PREFIX + "/managedEnvironments/cosmaneura-prod-app-environment"
HOST = "app.cosmaneura.com"
ROLES = {
    "api": ("cosmaneura-prod-app", "cosmaneura-prod-app-web"),
    "worker": ("cosmaneura-prod-worker", "cosmaneura-prod-app-worker"),
    "scheduler": ("cosmaneura-prod-scheduler", "cosmaneura-prod-app-scheduler"),
}
MAX_REVISIONS = 100
MAX_AGE = 7 * 24 * 3600
MAX_INTERVAL = 300
MAX_PANEL = 1024 * 1024
CAPTURE_STEP = "Record the actual Core/frontend runtime binding"
UPLOAD_STEP = "Retain the authenticated Core runtime binding"
DEPLOY_STEP = "Deploy the previously verified Core digest without rebuilding"
REDACTION = "structure-and-source-public-projection-v1"
UNPROVED = [
    "complete-configuration-equality",
    "complete-served-frontend-tree",
    "database-schema-and-tls",
    "production-entra-provider-role",
    "provider-domain-and-tls",
    "recovery-rehearsal",
]
require = candidate.require
canonical = candidate.canonical
digest = candidate.digest


def object_value(value: Any) -> dict[str, Any]:
    require(type(value) is dict)
    return value


def rows(value: Any, maximum: int = MAX_REVISIONS) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) <= maximum)
    return [object_value(item) for item in value]


def token(value: Any, pattern: str) -> str:
    require(type(value) is str and re.fullmatch(pattern, value))
    return value


def timestamp(value: Any) -> dt.datetime:
    token(value, r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def stamp(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def local_source() -> dict[str, Any]:
    git = ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(ROOT)]
    sha = (
        candidate.command([*git, "rev-parse", "--verify", "HEAD^{commit}"])
        .decode()
        .strip()
    )
    tree = (
        candidate.command([*git, "rev-parse", "--verify", "HEAD^{tree}"])
        .decode()
        .strip()
    )
    require(candidate.SHA.fullmatch(sha) and candidate.SHA.fullmatch(tree))
    candidate.command([*git, "diff", "--quiet", "HEAD", "--"])
    require(
        not candidate.command(
            [*git, "ls-files", "--others", "--exclude-standard"]
        ).strip()
    )
    require(
        candidate.api(f"repos/{candidate.REPOSITORY}/git/ref/heads/master")["object"][
            "sha"
        ]
        == sha
    )
    return {"source_sha": sha, "source_tree": tree}


def contract_digest() -> str:
    return digest((ROOT / SCRIPT).read_bytes())


def prepare(
    context: dict[str, Any], run_id: int, attempt: int, artifact_id: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_id, attempt, artifact_id = (
        candidate.positive(v) for v in (run_id, attempt, artifact_id)
    )
    prefix = f"repos/{candidate.REPOSITORY}/actions"
    run = candidate.api(f"{prefix}/runs/{run_id}")
    workflow = candidate.api(f"{prefix}/workflows/build_and_publish_local.yml")
    jobs = candidate.api(f"{prefix}/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    artifact = object_value(candidate.api(f"{prefix}/artifacts/{artifact_id}"))
    require(
        artifact.get("expired") is False
        and type(artifact.get("size_in_bytes")) is int
        and 0 < artifact["size_in_bytes"] <= candidate.MAX_ARCHIVE
    )
    raw = candidate.command(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            f"{prefix}/artifacts/{artifact_id}/zip",
        ],
        github=True,
    )
    record = candidate.verify_evidence(
        context, run_id, attempt, artifact_id, run, workflow, jobs, artifact, raw
    )
    return record, {
        "run_id": run_id,
        "run_attempt": attempt,
        "artifact_id": artifact_id,
        "archive_digest": artifact["digest"],
    }


def source_panel(record: dict[str, Any]) -> dict[str, Any]:
    panel = candidate.panel_identity(ROOT / "web/public")
    require(panel == record["panel"])
    return panel


def panel_https(panel: dict[str, Any]) -> dict[str, Any]:
    require(not any(os.environ.get(key) for key in ("SSL_CERT_FILE", "SSL_CERT_DIR")))
    sha = token(panel["sha256"], r"[0-9a-f]{64}")
    path = f"/vendor/cosmaneura-panel.v1.{sha}.js"
    # Fixed host/path, system CA and hostname validation; no proxy, auth header,
    # cookie, caller URL, redirect following, or unverified SSL context.
    connection = http.client.HTTPSConnection(
        HOST, timeout=20, context=ssl.create_default_context()
    )
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/javascript",
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
            },
        )
        response = connection.getresponse()
        require(response.status == 200 and response.getheader("Location") is None)
        require(response.getheader("Content-Encoding") in (None, "identity"))
        length = response.getheader("Content-Length")
        require(
            length is None
            or (re.fullmatch(r"[0-9]+", length) and 0 < int(length) <= MAX_PANEL)
        )
        raw = response.read(MAX_PANEL + 1)
        require(0 < len(raw) <= MAX_PANEL and digest(raw) == "sha256:" + sha)
        require(length is None or int(length) == len(raw))
    finally:
        connection.close()
    return {
        "origin": "https://" + HOST,
        "path": path,
        "sha256": sha,
        "bytes": len(raw),
        "transport": "system-ca-hostname-https-no-redirect",
    }


def structure(value: Any, depth: int = 0) -> Any:
    """Erase ALL scalar values before hashing; no low-entropy secret hashes.

    The explicitly constructed source-public projection below retains image,
    process role, resource identity, routing, env names and secret references.
    This shape fingerprint cannot prove equality of erased env/secret values.
    """
    require(depth <= 16)
    if type(value) is dict:
        require(len(value) <= 256)
        return {
            token(key, r"[A-Za-z_][A-Za-z0-9_.-]{0,127}"): structure(item, depth + 1)
            for key, item in sorted(value.items())
        }
    if type(value) is list:
        require(len(value) <= 256)
        return [structure(item, depth + 1) for item in value]
    require(value is None or type(value) in (str, int, float, bool))
    return {"erased_type": type(value).__name__}


def environment_entries(container: dict[str, Any]) -> list[dict[str, str]]:
    entries = rows(container.get("env", []), 256)
    projection = []
    seen = set()
    for entry in entries:
        require(set(entry) <= {"name", "value", "secretRef"})
        name = token(entry.get("name"), r"[A-Z][A-Z0-9_]{0,127}")
        require(name not in seen)
        seen.add(name)
        require(
            (entry.get("value") is not None) != (entry.get("secretRef") is not None)
        )
        if entry.get("secretRef") is not None:
            reference = token(entry["secretRef"], r"[a-z0-9][a-z0-9-]{0,127}")
            projection.append({"name": name, "secretRef": reference})
        else:
            require(type(entry["value"]) is str)
            if name == "PYTHON_ENV":
                require(entry["value"] == "production")
            projection.append({"name": name, "value": "erased"})
    require(
        "PYTHON_ENV" in seen
        and any(
            item == {"name": "PYTHON_ENV", "value": "erased"} for item in projection
        )
    )
    return sorted(projection, key=lambda item: item["name"])


def process(role: str) -> str:
    matches = re.findall(
        r"^" + role + r": ([^\r\n]+)$", (ROOT / "Procfile").read_text(), re.MULTILINE
    )
    require(len(matches) == 1)
    return matches[0]


def template_projection(template: Any, role: str, image: str) -> dict[str, Any]:
    template = object_value(template)
    require(not template.get("initContainers") and not template.get("volumes"))
    containers = rows(template.get("containers"), 1)
    require(len(containers) == 1)
    container = containers[0]
    require(container.get("name") == ROLES[role][1] and container.get("image") == image)
    require(
        container.get("command") in (None, [])
        and container.get("args") == [process(role)]
    )
    return {
        "redaction": REDACTION,
        "shape": structure(template),
        "container": ROLES[role][1],
        "image": image,
        "process_role": role,
        "environment_names_and_references": environment_entries(container),
        "public_environment": {"PYTHON_ENV": "production"},
    }


def az(name: str, arguments: list[str]) -> Any:
    require(name in {item[0] for item in ROLES.values()})
    require(
        arguments == ["show"]
        or arguments[:2]
        in (["revision", "list"], ["revision", "show"], ["replica", "list"])
    )
    raw = candidate.command(
        [
            "az",
            "containerapp",
            *arguments,
            "--name",
            name,
            "--resource-group",
            GROUP,
            "--subscription",
            SUBSCRIPTION,
            "--only-show-errors",
            "--output",
            "json",
        ]
    )
    return candidate.decode(raw, maximum=candidate.MAX_METADATA)


def public_resource(value: Any, prefix: str) -> str:
    value = token(value, r"/[A-Za-z0-9_./-]{1,512}")
    require(value.startswith(prefix + "/") and ".." not in value)
    return value


def config_projection(app: dict[str, Any], role: str, revision: str) -> dict[str, Any]:
    properties = object_value(app["properties"])
    configuration = object_value(properties["configuration"])
    require(configuration.get("activeRevisionsMode") == "Single")
    identities = object_value(app.get("identity", {}))
    identity_ids = sorted(object_value(identities.get("userAssignedIdentities", {})))
    for identity in identity_ids:
        public_resource(
            identity,
            f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.ManagedIdentity/userAssignedIdentities",
        )
    secret_names = [
        token(item.get("name"), r"[a-z0-9][a-z0-9-]{0,127}")
        for item in rows(configuration.get("secrets", []), 256)
    ]
    require(len(secret_names) == len(set(secret_names)))
    registries = rows(configuration.get("registries", []), 1)
    require(
        len(registries) == 1
        and registries[0].get("server") == candidate.REGISTRY + ".azurecr.io"
    )
    registry = {"server": candidate.REGISTRY + ".azurecr.io"}
    if registries[0].get("passwordSecretRef") is not None:
        reference = token(
            registries[0]["passwordSecretRef"], r"[a-z0-9][a-z0-9-]{0,127}"
        )
        require(reference in secret_names)
        registry["passwordSecretRef"] = reference
    elif registries[0].get("identity") is not None:
        registry["identity"] = public_resource(
            registries[0]["identity"],
            f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.ManagedIdentity",
        )
    else:
        require(False)
    ingress = configuration.get("ingress")
    traffic: list[dict[str, Any]] = []
    if role == "api":
        ingress = object_value(ingress)
        require(
            ingress.get("external") is True
            and ingress.get("targetPort") == 80
            and ingress.get("allowInsecure") is False
        )
        routes = rows(ingress.get("traffic"), 1)
        require(
            len(routes) == 1
            and type(routes[0].get("weight")) is int
            and routes[0]["weight"] == 100
        )
        route = routes[0]
        require(not route.get("label"))
        if route.get("latestRevision") is True:
            require(route.get("revisionName") in (None, "", revision))
            require(properties.get("latestReadyRevisionName") == revision)
        else:
            require(
                route.get("latestRevision") in (None, False)
                and route.get("revisionName") == revision
            )
        traffic = [{"revision": revision, "weight": 100}]
    else:
        require(ingress is None)
    return {
        "redaction": REDACTION,
        "shape": structure(configuration),
        "mode": "Single",
        "traffic": traffic,
        "secret_names": sorted(secret_names),
        "registry": registry,
        "identity_type": token(
            identities.get("type", "None"),
            r"None|UserAssigned|SystemAssigned|SystemAssigned, UserAssigned",
        ),
        "identity_ids": identity_ids,
    }


def observe_app(role: str, context: dict[str, Any], image: str) -> dict[str, Any]:
    name, container_name = ROLES[role]
    revision = f"{name}--r{context['run_id']}a{context['run_attempt']}-{context['source_sha'][:7]}"
    require(len(revision) <= 54)
    app = object_value(az(name, ["show"]))
    require(
        app.get("id") == PREFIX + "/containerApps/" + name and app.get("name") == name
    )
    properties = object_value(app["properties"])
    environments = [
        properties[key]
        for key in ("environmentId", "managedEnvironmentId")
        if properties.get(key) is not None
    ]
    require(environments and all(value == ENVIRONMENT for value in environments))
    require(properties.get("provisioningState") == "Succeeded")
    require(
        properties.get("latestRevisionName")
        == properties.get("latestReadyRevisionName")
        == revision
    )
    revisions = rows(
        az(
            name,
            [
                "revision",
                "list",
                "--query",
                "[].{id:id,name:name,properties:{active:properties.active}}",
            ],
        )
    )
    require(
        revisions and len({item.get("name") for item in revisions}) == len(revisions)
    )
    require(
        all(
            type(object_value(item.get("properties")).get("active")) is bool
            for item in revisions
        )
    )
    selected = [
        item
        for item in revisions
        if object_value(item.get("properties")).get("active") is True
    ]
    require(len(selected) == 1 and selected[0].get("name") == revision)
    selected_revision = object_value(
        az(name, ["revision", "show", "--revision", revision])
    )
    require(
        selected_revision.get("id")
        == PREFIX + "/containerApps/" + name + "/revisions/" + revision
        and selected_revision.get("name") == revision
    )
    active = object_value(selected_revision["properties"])
    require(active.get("active") is True)
    require(
        active.get("healthState") == "Healthy"
        and active.get("provisioningState") == "Provisioned"
        and active.get("runningState") in ("Running", "RunningAtMaxScale")
    )
    app_template = template_projection(properties["template"], role, image)
    revision_template = template_projection(active["template"], role, image)
    require(app_template == revision_template)
    configuration = config_projection(app, role, revision)
    replicas = []
    for item in revisions:
        revision_name = token(item.get("name"), re.escape(name) + r"--[a-z0-9-]{1,45}")
        require(
            item.get("id")
            == PREFIX + "/containerApps/" + name + "/revisions/" + revision_name
        )
        found = rows(
            az(
                name,
                [
                    "replica",
                    "list",
                    "--revision",
                    revision_name,
                    "--query",
                    "[].{name:name,properties:{containers:properties.containers[].{name:name,ready:ready}}}",
                ],
            ),
            100,
        )
        require(len({replica.get("name") for replica in found}) == len(found))
        if revision_name != revision:
            require(role == "api" or not found)
            continue
        for replica in found:
            replica_name = token(replica.get("name"), r"[a-z0-9][a-z0-9-]{0,127}")
            ready = rows(object_value(replica["properties"]).get("containers"), 1)
            require(
                len(ready) == 1
                and ready[0].get("name") == container_name
                and ready[0].get("ready") is True
            )
            replicas.append(replica_name)
    require(replicas and (role == "api" or len(replicas) == 1))
    return {
        "name": name,
        "revision": revision,
        "image": image,
        "process_role": role,
        "ready_replicas": sorted(replicas),
        "sanitized_template_digest": digest(canonical(app_template)),
        "sanitized_configuration_digest": digest(canonical(configuration)),
        "redaction": REDACTION,
    }


@contextmanager
def observation_deadline() -> Iterator[None]:
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    previous = signal.getsignal(signal.SIGALRM)

    def expired(_number: int, _frame: Any) -> None:
        raise candidate.CandidateRefused("production_core_runtime_binding_refused")

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, MAX_INTERVAL)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def observe(context: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    with observation_deadline():
        result = _observe(context, record)
        require(0 <= time.monotonic() - started <= MAX_INTERVAL)
        return result


def _observe(context: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    account = object_value(
        candidate.decode(
            candidate.command(
                [
                    "az",
                    "account",
                    "show",
                    "--subscription",
                    SUBSCRIPTION,
                    "--only-show-errors",
                    "--output",
                    "json",
                ]
            ),
            maximum=candidate.MAX_METADATA,
        )
    )
    require(account.get("id") == SUBSCRIPTION and account.get("tenantId") == TENANT)
    started = now()
    first = {role: observe_app(role, context, record["image"]) for role in ROLES}
    panel = panel_https(source_panel(record))
    second = {role: observe_app(role, context, record["image"]) for role in ROLES}
    ended = now()
    require(first == second and 0 <= (ended - started).total_seconds() <= MAX_INTERVAL)
    return {
        "started_at": stamp(started),
        "completed_at": stamp(ended),
        "subscription": SUBSCRIPTION,
        "tenant": TENANT,
        "environment": ENVIRONMENT,
        "workloads": first,
        "panel_https": panel,
    }


def validate_observation(
    value: Any, context: dict[str, Any], record: dict[str, Any]
) -> dict[str, Any]:
    value = candidate.object_fields(
        value,
        {
            "started_at",
            "completed_at",
            "subscription",
            "tenant",
            "environment",
            "workloads",
            "panel_https",
        },
    )
    started, ended = timestamp(value["started_at"]), timestamp(value["completed_at"])
    require(
        0 <= (ended - started).total_seconds() <= MAX_INTERVAL
        and 0 <= (now() - ended).total_seconds() <= MAX_AGE
    )
    require(
        value["subscription"] == SUBSCRIPTION
        and value["tenant"] == TENANT
        and value["environment"] == ENVIRONMENT
    )
    workloads = candidate.object_fields(value["workloads"], set(ROLES))
    for role, (name, _) in ROLES.items():
        item = candidate.object_fields(
            workloads[role],
            {
                "name",
                "revision",
                "image",
                "process_role",
                "ready_replicas",
                "sanitized_template_digest",
                "sanitized_configuration_digest",
                "redaction",
            },
        )
        require(
            item["name"] == name
            and item["process_role"] == role
            and item["image"] == record["image"]
            and item["redaction"] == REDACTION
        )
        require(
            item["revision"]
            == f"{name}--r{context['run_id']}a{context['run_attempt']}-{context['source_sha'][:7]}"
        )
        replicas = item["ready_replicas"]
        require(
            type(replicas) is list
            and 0 < len(replicas) <= 100
            and (role == "api" or len(replicas) == 1)
        )
        require(
            all(
                type(replica) is str
                and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", replica)
                for replica in replicas
            )
            and replicas == sorted(set(replicas))
        )
        for key in ("sanitized_template_digest", "sanitized_configuration_digest"):
            require(type(item[key]) is str and candidate.DIGEST.fullmatch(item[key]))
    panel = candidate.object_fields(
        value["panel_https"], {"origin", "path", "sha256", "bytes", "transport"}
    )
    require(
        panel["origin"] == "https://" + HOST
        and panel["sha256"] == record["panel"]["sha256"]
        and panel["path"] == f"/vendor/cosmaneura-panel.v1.{panel['sha256']}.js"
    )
    require(
        type(panel["bytes"]) is int
        and 0 < panel["bytes"] <= MAX_PANEL
        and panel["transport"] == "system-ca-hostname-https-no-redirect"
    )
    return value


def binding_record(
    context: dict[str, Any],
    record: dict[str, Any],
    association: dict[str, Any],
    observation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "purpose": "sampled-production-core-runtime-binding",
        "repository": candidate.REPOSITORY,
        "workflow_ref": candidate.WORKFLOW_REF,
        **context,
        "contract_digest": contract_digest(),
        "prepare": association,
        "candidate": record,
        "observation": validate_observation(observation, context, record),
        "unproved": UNPROVED,
        "deployment_authorized": False,
    }


def unpack(raw: bytes, expected: str) -> Any:
    require(0 < len(raw) <= candidate.MAX_ARCHIVE and digest(raw) == expected)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1)
        item = entries[0]
        require(
            item.filename == "runtime-binding.json"
            and not item.is_dir()
            and not item.flag_bits & 1
            and not stat.S_ISLNK(item.external_attr >> 16)
            and item.file_size <= candidate.MAX_JSON
        )
        with archive.open(item) as stream:
            return candidate.decode(stream.read(candidate.MAX_JSON + 1))


def verify_promote(
    context: dict[str, Any],
    artifact_id: int,
    run: Any,
    workflow: Any,
    jobs: Any,
    artifact: Any,
    raw: bytes,
) -> dict[str, Any]:
    run, workflow, jobs, artifact = (
        object_value(value) for value in (run, workflow, jobs, artifact)
    )
    run_id, attempt = context["run_id"], context["run_attempt"]
    require(
        run.get("id") == run_id
        and run.get("run_attempt") == attempt
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
    )
    require(candidate.positive(run.get("run_attempt")) == attempt)
    require(
        run.get("event") == "workflow_dispatch"
        and run.get("head_branch") == "master"
        and run.get("head_sha") == context["source_sha"]
    )
    require(
        run["repository"]["full_name"] == candidate.REPOSITORY
        and run["actor"]["login"] == run["triggering_actor"]["login"] == "JohnClem"
    )
    require(
        workflow.get("path") == candidate.WORKFLOW
        and workflow.get("state") == "active"
        and workflow.get("id") == run.get("workflow_id")
        and run.get("path") == candidate.WORKFLOW
    )
    expected = candidate.required_jobs() | {"🚀 Deploy"}
    observed = rows(jobs.get("jobs"), 100)
    require(
        jobs.get("total_count") == len(expected) == len(observed)
        and len({item.get("id") for item in observed}) == len(observed)
        and {item.get("name") for item in observed} == expected
    )
    for item in observed:
        require(candidate.positive(item.get("run_attempt")) == attempt)
        require(
            item.get("run_id") == run_id
            and item.get("run_attempt") == attempt
            and item.get("head_sha") == context["source_sha"]
            and item.get("status") == "completed"
            and item.get("conclusion")
            == (
                "skipped"
                if item.get("name") == "Prepare production candidate"
                else "success"
            )
        )
    deploy = next(item for item in observed if item["name"] == "🚀 Deploy")
    steps = rows(deploy.get("steps"), 100)
    selected = []
    for name in (DEPLOY_STEP, CAPTURE_STEP, UPLOAD_STEP):
        found = [item for item in steps if item.get("name") == name]
        require(
            len(found) == 1
            and found[0].get("status") == "completed"
            and found[0].get("conclusion") == "success"
        )
        selected.append(candidate.positive(found[0].get("number")))
    require(selected == sorted(set(selected)))
    require(
        artifact.get("id") == artifact_id
        and artifact.get("expired") is False
        and artifact.get("name")
        == f"production-core-runtime-binding-{run_id}-{attempt}-{context['source_sha']}"
    )
    require(
        type(artifact.get("size_in_bytes")) is int
        and 0 < artifact["size_in_bytes"] <= candidate.MAX_ARCHIVE
        and type(artifact.get("digest")) is str
        and candidate.DIGEST.fullmatch(artifact["digest"])
    )
    association = object_value(artifact.get("workflow_run"))
    require(
        association.get("id") == run_id
        and association.get("head_sha") == context["source_sha"]
        and association.get("head_branch") == "master"
        and association.get("repository_id")
        == association.get("head_repository_id")
        == run["repository"]["id"]
    )
    value = candidate.object_fields(
        unpack(raw, artifact["digest"]),
        {
            "schema_version",
            "purpose",
            "repository",
            "workflow_ref",
            "source_sha",
            "source_tree",
            "run_id",
            "run_attempt",
            "contract_digest",
            "prepare",
            "candidate",
            "observation",
            "unproved",
            "deployment_authorized",
        },
    )
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["purpose"] == "sampled-production-core-runtime-binding"
    )
    require(
        value["repository"] == candidate.REPOSITORY
        and value["workflow_ref"] == candidate.WORKFLOW_REF
        and all(value[key] == context[key] for key in context)
    )
    require(candidate.positive(value["run_attempt"]) == attempt)
    require(
        value["contract_digest"] == contract_digest()
        and value["unproved"] == UNPROVED
        and value["deployment_authorized"] is False
    )
    prepared = candidate.object_fields(
        value["prepare"], {"run_id", "run_attempt", "artifact_id", "archive_digest"}
    )
    producer, accepted = prepare(
        context, prepared["run_id"], prepared["run_attempt"], prepared["artifact_id"]
    )
    require(accepted == prepared and value["candidate"] == producer)
    source_panel(producer)
    observation = validate_observation(value["observation"], context, producer)
    require(
        timestamp(deploy["started_at"])
        <= timestamp(observation["started_at"])
        <= timestamp(observation["completed_at"])
        <= timestamp(deploy["completed_at"])
    )
    return value


def admit(run_id: int, attempt: int, artifact_id: int) -> dict[str, Any]:
    run_id, attempt, artifact_id = (
        candidate.positive(v) for v in (run_id, attempt, artifact_id)
    )
    context = {**local_source(), "run_id": run_id, "run_attempt": attempt}
    prefix = f"repos/{candidate.REPOSITORY}/actions"
    run = candidate.api(f"{prefix}/runs/{run_id}")
    workflow = candidate.api(f"{prefix}/workflows/build_and_publish_local.yml")
    jobs = candidate.api(f"{prefix}/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    artifact = object_value(candidate.api(f"{prefix}/artifacts/{artifact_id}"))
    require(
        artifact.get("expired") is False
        and type(artifact.get("size_in_bytes")) is int
        and 0 < artifact["size_in_bytes"] <= candidate.MAX_ARCHIVE
    )
    raw = candidate.command(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            f"{prefix}/artifacts/{artifact_id}/zip",
        ],
        github=True,
    )
    return verify_promote(context, artifact_id, run, workflow, jobs, artifact, raw)


def record(
    run_id: int, attempt: int, artifact_id: int, configuration: Path, output: Path
) -> None:
    context = candidate.source_context()
    prepared, accepted = prepare(context, run_id, attempt, artifact_id)
    config = candidate.public_configuration(candidate.read_json(configuration))
    require(digest(canonical(config)) == prepared["public_configuration_digest"])
    observation = observe(context, prepared)
    require(candidate.source_context() == context)
    value = binding_record(context, prepared, accepted, observation)
    raw = canonical(value) + b"\n"
    require(len(raw) <= candidate.MAX_JSON)
    descriptor = os.open(
        output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def persistent_binding(value: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: item
        for key, item in value.items()
        if key not in {"started_at", "completed_at"}
    }
    result["workloads"] = {
        role: {key: field for key, field in item.items() if key != "ready_replicas"}
        for role, item in value["workloads"].items()
    }
    return result


def check(run_id: int, attempt: int, artifact_id: int) -> dict[str, Any]:
    value = admit(run_id, attempt, artifact_id)
    context = {
        key: value[key]
        for key in ("source_sha", "source_tree", "run_id", "run_attempt")
    }
    fresh = observe(context, value["candidate"])
    require(persistent_binding(fresh) == persistent_binding(value["observation"]))
    require(
        local_source() == {key: context[key] for key in ("source_sha", "source_tree")}
    )
    return {
        "purpose": "fresh-authenticated-core-runtime-binding-only",
        "source_sha": value["source_sha"],
        "image": value["candidate"]["image"],
        "promote_run_id": run_id,
        "promote_run_attempt": attempt,
        "artifact_id": artifact_id,
        "observation": fresh,
        "unproved": UNPROVED,
        "deployment_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("record", "check"))
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--artifact-id", type=int, required=True)
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "record":
            require(args.configuration is not None and args.output is not None)
            record(
                args.run_id,
                args.run_attempt,
                args.artifact_id,
                args.configuration,
                args.output,
            )
        else:
            require(args.configuration is None and args.output is None)
            print(
                canonical(
                    check(args.run_id, args.run_attempt, args.artifact_id)
                ).decode()
            )
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
        http.client.HTTPException,
    ):
        print("production_core_runtime_binding_refused", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
