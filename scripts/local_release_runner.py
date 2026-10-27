#!/usr/bin/env python3
"""Run one authorized GitHub release job on a disposable local Docker runner.

No Actions credentials are written to disk or printed. An immutable pre-job hook
admits only this repository's three reviewed master workflows at the pinned SHA.
"""

import argparse
import hashlib
import json
import subprocess
import uuid
from pathlib import Path

REPOSITORY = "CosmaNeura/cosmaneura"
WORKFLOWS = {
    ".github/workflows/infra_production_plan_local.yml",
    ".github/workflows/infra_apply_local.yml",
    ".github/workflows/build_and_publish_local.yml",
}
LABEL = "cosmaneura-production-local"


def github(path: str, body: dict | None = None) -> dict:
    args = ["gh", "api", path]
    if body is not None:
        args += ["--method", "POST", "--input", "-"]
    result = subprocess.run(
        args,
        input=json.dumps(body) if body is not None else None,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        # API responses can contain credentials; never echo raw response bodies.
        raise SystemExit(f"GitHub request failed: {path} (exit {result.returncode}).")
    return json.loads(result.stdout)


def cleanup(name: str) -> None:
    subprocess.run(
        ["docker", "rm", "--force", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    networks = subprocess.check_output(
        [
            "docker",
            "network",
            "ls",
            "--filter",
            f"label=cosmaneura.local-runner={name}",
            "-q",
        ],
        text=True,
    ).split()
    for network in networks:
        metadata = json.loads(
            subprocess.check_output(
                ["docker", "network", "inspect", network], text=True
            )
        )[0]
        project = "cosma-local-tests-" + hashlib.sha256(name.encode()).hexdigest()[:16]
        if (
            metadata["Name"] != project
            or metadata.get("Labels", {}).get("cosmaneura.local-runner") != name
        ):
            raise SystemExit("Refusing cleanup of an unexpected network identity.")
        containers = list(metadata.get("Containers") or {})
        if containers:
            attached = json.loads(
                subprocess.check_output(["docker", "inspect", *containers], text=True)
            )
            if any(
                item["Config"].get("Labels", {}).get("com.docker.compose.project")
                != project
                for item in attached
            ):
                raise SystemExit(
                    "Refusing cleanup of another project's attached container."
                )
        for container in containers:
            subprocess.run(
                ["docker", "rm", "--force", container],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        volumes = subprocess.check_output(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "-q",
            ],
            text=True,
        ).split()
        for volume in volumes:
            subprocess.run(
                ["docker", "volume", "rm", volume],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        subprocess.run(
            ["docker", "network", "rm", network], check=True, stdout=subprocess.DEVNULL
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", type=int)
    parser.add_argument(
        "--dns",
        action="append",
        default=[],
        help="Optional DNS server for this task container only",
    )
    parser.add_argument(
        "--image",
        required=True,
        help="Locally built runner image pinned by sha256: image ID",
    )
    args = parser.parse_args()
    if not args.image.startswith("sha256:") or len(args.image) != 71:
        raise SystemExit(
            "Pin the locally verified runner image by full sha256 image ID."
        )
    login = github("user")["login"]
    master = github(f"repos/{REPOSITORY}/git/ref/heads/master")["object"]["sha"]
    run = github(f"repos/{REPOSITORY}/actions/runs/{args.run_id}")
    if (
        login != "JohnClem"
        or run["event"] != "workflow_dispatch"
        or run["head_branch"] != "master"
        or run["head_sha"] != master
        or run["path"] not in WORKFLOWS
        or run["actor"]["login"] != "JohnClem"
        or run["triggering_actor"]["login"] != "JohnClem"
        or run["status"] not in ("queued", "in_progress", "waiting")
    ):
        raise SystemExit(
            "Only an active founder-authorized release run at exact current master is accepted."
        )
    metadata = json.loads(
        subprocess.check_output(["docker", "image", "inspect", args.image], text=True)
    )[0]
    if (
        metadata["Architecture"] != "amd64"
        or metadata["Config"]["User"] != "runner"
        or metadata["Config"]["Entrypoint"]
        != ["/usr/local/bin/local-runner-entrypoint"]
        or "ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/local-release-job-started"
        not in metadata["Config"]["Env"]
    ):
        raise SystemExit("The image is not the reviewed local release runner.")
    files = {
        "/usr/local/bin/local-release-job-started": "job-started.sh",
        "/usr/local/bin/local-runner-entrypoint": "entrypoint.sh",
    }
    actual_hashes = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--entrypoint",
            "sha256sum",
            args.image,
            *files,
        ],
        text=True,
    )
    for line in actual_hashes.splitlines():
        digest, path = line.split()
        expected = hashlib.sha256(
            (Path(__file__).parent / "local-runner" / files[path]).read_bytes()
        ).hexdigest()
        if digest != expected:
            raise SystemExit("The runner's immutable admission source has changed.")
    name = "cosma-local-runner-" + uuid.uuid4().hex[:16]
    response = github(
        f"repos/{REPOSITORY}/actions/runners/generate-jitconfig",
        {
            "name": name,
            "runner_group_id": 1,
            "labels": ["self-hosted", "Linux", "X64", LABEL, f"{LABEL}-{args.run_id}"],
            "work_folder": "_work",
        },
    )
    runner_id = response["runner"]["id"]
    try:
        socket_group = subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "stat",
                "--volume",
                "/var/run/docker.sock:/var/run/docker.sock",
                args.image,
                "-c",
                "%g",
                "/var/run/docker.sock",
            ],
            text=True,
        ).strip()
        if not socket_group.isdigit():
            raise SystemExit("Could not determine the local Docker socket group.")
        command = [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--name",
            name,
            "--label",
            "cosmaneura.local-release=true",
            "--platform",
            "linux/amd64",
            "--group-add",
            socket_group,
            "--volume",
            "/var/run/docker.sock:/var/run/docker.sock",
            "--env",
            f"LOCAL_RUNNER_CONTAINER={name}",
        ]
        for server in args.dns:
            command += ["--dns", server]
        command.append(args.image)
        # One JIT runner accepts one job and exits. Docker and JIT registrations
        # are removed even on interruption; no host home directory is mounted.
        result = subprocess.run(
            command,
            input=f"{master}\n{args.run_id}\n{run['run_attempt']}\n{response['encoded_jit_config']}\n",
            text=True,
        )
        if result.returncode:
            raise SystemExit(f"Local runner exited with status {result.returncode}.")
    finally:
        try:
            cleanup(name)
        finally:
            removal = subprocess.run(
                [
                    "gh",
                    "api",
                    "--method",
                    "DELETE",
                    f"repos/{REPOSITORY}/actions/runners/{runner_id}",
                ],
                capture_output=True,
            )
            if removal.returncode and b"404" not in removal.stderr:
                print("Local runner registration cleanup requires operator inspection.")


if __name__ == "__main__":
    main()
