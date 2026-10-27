#!/usr/bin/env python3
"""Isolated, unpublished test services for one ephemeral local Actions job."""

import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def main() -> None:
    runner = os.environ.get("LOCAL_RUNNER_CONTAINER", "")
    if not re.fullmatch(r"cosma-local-runner-[a-f0-9]{16}", runner):
        raise SystemExit("An operator-created ephemeral local runner is required.")
    state_path = Path(os.environ["RUNNER_TEMP"]) / "local-services.json"
    if sys.argv[1:] == ["stop"]:
        if not state_path.exists():
            return
        state = json.loads(state_path.read_text())
        project = state["project"]
        if not re.fullmatch(r"cosma-local-tests-[a-f0-9]{16}", project):
            raise SystemExit("Invalid local test project identity.")
        owner = docker(
            "network",
            "inspect",
            project,
            "--format",
            '{{index .Labels "cosmaneura.local-runner"}}',
        )
        if owner != runner:
            raise SystemExit("Refusing cleanup of another runner's resources.")
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                state["source"],
                "-f",
                state["override"],
                "down",
                "--volumes",
            ],
            check=True,
        )
        docker("network", "disconnect", project, runner)
        docker("network", "rm", project)
        state_path.unlink()
        return
    if sys.argv[1:] != ["start"] or state_path.exists():
        raise SystemExit("Expected start or stop for one local job.")
    project = "cosma-local-tests-" + hashlib.sha256(runner.encode()).hexdigest()[:16]
    network_ids = docker("network", "ls", "-q").split()
    networks = json.loads(docker("network", "inspect", *network_ids))
    occupied = [
        ipaddress.ip_network(config["Subnet"])
        for network in networks
        for config in (network.get("IPAM", {}).get("Config") or [])
        if config.get("Subnet")
    ]
    subnet = next(
        (
            f"10.252.{index}.0/24"
            for index in range(1, 255)
            if not any(
                ipaddress.ip_network(f"10.252.{index}.0/24").overlaps(other)
                for other in occupied
                if other.version == 4
            )
        ),
        None,
    )
    if subnet is None:
        raise SystemExit("No isolated local test subnet is available.")
    source = str(Path("docker-compose.yml").resolve())
    override = Path(os.environ["RUNNER_TEMP"]) / "local-compose.yml"
    override.write_text(
        "services:\n"
        + "".join(
            f"  {service}:\n    ports: !reset []\n    restart: 'no'\n"
            for service in ("postgres", "redis", "mailpit")
        )
        + f"networks:\n  default:\n    external: true\n    name: {project}\n"
    )
    docker(
        "network",
        "create",
        "--internal",
        "--subnet",
        subnet,
        "--label",
        f"cosmaneura.local-runner={runner}",
        project,
    )
    state_path.write_text(
        json.dumps({"project": project, "source": source, "override": str(override)})
    )
    state_path.chmod(0o600)
    docker("network", "connect", project, runner)
    # These credentials are exclusively synthetic test values from .env.shared.
    environment = {
        **os.environ,
        "POSTGRES_USER": "root",
        "POSTGRES_PASSWORD": "password",
        "POSTGRES_DB": "development",
    }
    subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            source,
            "-f",
            str(override),
            "up",
            "--detach",
            "--wait",
            "--wait-timeout",
            "90",
            "--pull",
            "missing",
        ],
        env=environment,
        check=True,
    )
    with open(os.environ["GITHUB_ENV"], "a") as target:
        for key, value in {
            "DATABASE_HOST": "postgres",
            "REDIS_HOST": "redis",
            "SMTP_HOST": "mailpit",
            "COMPOSE_PROJECT_NAME": project,
            "COMPOSE_FILE": f"{source}:{override}",
            "COSMANEURA_LOCAL_ACCEPTANCE": "1",
        }.items():
            target.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
