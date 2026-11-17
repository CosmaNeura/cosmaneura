"""Prepare-only refusal guard for Core's source-pinned Scribe API runtime.

Starts only a fixed, network-disabled filesystem/interpreter check. No
application entrypoint, configuration, plan, apply, candidate or promotion.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from scripts.scribe_runtime_oci import package
from scripts.scribe_runtime_pin import RUNTIME_DIGEST_LABEL, SCRIBE_RUNTIME_IMAGE_DIGEST

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "infra/production_live_settings.tf"
PYTHON = "/opt/scribe/runtime/bin/python"


def command(arguments: list[str]) -> str:
    environment = dict(os.environ)
    for key in (
        "OP_SERVICE_ACCOUNT_TOKEN",
        "OP_SOCK",
        "OP_CONNECT_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "SENTRY_AUTH_TOKEN",
        "SENTRY_ORG",
        "SENTRY_PROJECT",
    ):
        environment.pop(key, None)
    result = subprocess.run(
        arguments,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode:
        raise ValueError("scribe_runtime_command_rejected")
    return result.stdout.strip()


def runtime_required(source: str) -> bool:
    # Remove complete quoted strings and comments before looking at HCL
    # structure. Configuration JSON and comment examples cannot spoof a pin.
    scrubbed = re.sub(
        r'"(?:[^"\\]|\\.)*"|/\*[\s\S]*?\*/|\#[^\n]*|//[^\n]*',
        lambda match: "STRING" if match[0].startswith('"') else " ",
        source,
    )
    matches = list(
        re.finditer(r"\bproduction_live_scribe_core_bundle\s*=\s*\{", scrubbed)
    )
    if len(matches) != 1:
        raise ValueError("scribe_bundle_source_pin_invalid")
    start = matches[0].end()
    depth = 1
    end = start
    while end < len(scrubbed) and depth:
        depth += (scrubbed[end] == "{") - (scrubbed[end] == "}")
        end += 1
    if depth:
        raise ValueError("scribe_bundle_source_pin_invalid")
    block = scrubbed[start : end - 1]
    values = []
    level = 0
    for line in block.splitlines():
        if level == 0:
            values.extend(re.findall(r"^\s*api_enabled\s*=\s*([^\n]+)", line))
        level += line.count("{") - line.count("}")
    if len(values) != 1 or values[0].strip() not in {"true", "false"}:
        raise ValueError("scribe_bundle_source_pin_invalid")
    return values[0].strip() == "true"


def verify_runtime(root: Path, layered_digest: str) -> str:
    launcher = root / "bin/python"
    if (
        launcher.is_symlink()
        or not launcher.is_file()
        or not launcher.stat().st_mode & 0o111
    ):
        raise ValueError("scribe_runtime_python_missing_or_not_executable")
    if layered_digest != SCRIBE_RUNTIME_IMAGE_DIGEST:
        raise ValueError("scribe_runtime_layer_digest_rejected")
    actual = package(root)
    if actual != SCRIBE_RUNTIME_IMAGE_DIGEST:
        raise ValueError("scribe_runtime_content_digest_rejected")
    return actual


def verify_image(image: str) -> str:
    inspected = json.loads(command(["docker", "image", "inspect", image]))
    if type(inspected) is not list or len(inspected) != 1:
        raise ValueError("scribe_runtime_image_ambiguous")
    value = inspected[0]
    identity = value["Id"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
        raise ValueError("scribe_runtime_image_identity_invalid")
    label = (value["Config"].get("Labels") or {}).get(RUNTIME_DIGEST_LABEL)
    if label != SCRIBE_RUNTIME_IMAGE_DIGEST:
        raise ValueError("scribe_runtime_layer_digest_rejected")
    container = command(
        ["docker", "create", "--network", "none", "--entrypoint", "/bin/true", identity]
    )
    try:
        with tempfile.TemporaryDirectory(prefix="core-scribe-check-") as temporary:
            runtime = Path(temporary) / "runtime"
            command(["docker", "cp", container + ":/opt/scribe/runtime", str(runtime)])
            actual = verify_runtime(runtime, label)
    finally:
        command(["docker", "rm", container])
    # Test permissions as Core's actual image user, and check that the copied
    # interpreter uses Core's exact Python closure. No app or provider imports.
    command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--entrypoint",
            "/bin/sh",
            identity,
            "-c",
            "test -x /opt/scribe/runtime/bin/python && "
            "cmp /opt/venv/bin/python /opt/scribe/runtime/bin/python-real && "
            "/usr/bin/env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 "
            "/opt/scribe/runtime/bin/python -c 'import sys; "
            'assert sys.version_info[:2] == (3, 13); assert sys.prefix == "/opt/scribe/runtime"\'',
        ]
    )
    return actual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        if runtime_required(PIN.read_text()):
            print(verify_image(args.image))
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print("scribe_runtime_candidate_refused")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
