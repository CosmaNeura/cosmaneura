"""Exercise real Redis through an owned networkless container, not a TCP client."""

import json
import os
import subprocess
from pathlib import Path

import pytest


class OwnedRedis:
    def __init__(self):
        self.container = os.environ.get("VISIT_PREP_OWNED_REDIS_CONTAINER")
        if self.container is None:
            raise RuntimeError("run the Visit Prep checks with --with-redis")
        root = Path(os.environ["VISIT_PREP_OWNED_TEST_DIRECTORY"])
        if (
            root.parent != Path("/private/tmp")
            or not root.name.startswith("cosma-vp-tests-")
            or not root.is_dir()
            or root.resolve() != root
            or self.container != root.name + "-redis"
        ):
            raise RuntimeError("Redis container is not owned by this test run")
        self.docker = os.environ["VISIT_PREP_TEST_DOCKER"]
        endpoint = os.environ["VISIT_PREP_TEST_DOCKER_HOST"]
        if not endpoint.startswith("unix:///"):
            raise RuntimeError("Redis tests require a local Docker socket")
        self.environment = {
            "PATH": os.defpath + ":/opt/homebrew/bin:/usr/local/bin",
            "DOCKER_HOST": endpoint,
        }
        objects = json.loads(self._docker("inspect", self.container))
        if (
            len(objects) != 1
            or objects[0]["Config"]["Labels"].get("cosma.visit-prep-test-owner")
            != str(root)
            or objects[0]["HostConfig"]["NetworkMode"] != "none"
            or not objects[0]["HostConfig"]["ReadonlyRootfs"]
            or any(mount["Type"] != "tmpfs" for mount in objects[0]["Mounts"])
        ):
            raise RuntimeError("Redis isolation could not be proven")

    def _docker(self, *arguments):
        return subprocess.run(
            [self.docker, *arguments],
            env=self.environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout

    def _command(self, *arguments):
        return self._docker(
            "exec",
            self.container,
            "redis-cli",
            "-s",
            "/tmp/visit-prep-redis.sock",
            "--raw",
            *(str(argument) for argument in arguments),
        ).splitlines()

    def eval(self, script, key_count, *arguments):
        return [
            int(value) for value in self._command("EVAL", script, key_count, *arguments)
        ]

    def delete(self, *keys):
        return int(self._command("DEL", *keys)[0])

    def mget(self, keys):
        return self._command("MGET", *keys)

    def expire(self, key, seconds):
        return bool(int(self._command("EXPIRE", key, seconds)[0]))

    def ttl(self, key):
        return int(self._command("TTL", key)[0])


@pytest.fixture
def owned_rate_limit_redis():
    return OwnedRedis()
