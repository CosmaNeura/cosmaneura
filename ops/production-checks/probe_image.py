"""Check the already-cached pinned image without network or credential inputs."""

import subprocess
import sys

from contract import IMAGE

PROBE = """
import sys
import psycopg
assert sys.executable == '/opt/venv/bin/python'
assert sys.version_info[:2] == (3, 13)
assert psycopg.__version__ == '3.2.6'
assert psycopg.pq.version() >= 170004
print('RUNTIME_SUPPORTED=1')
"""


def main():
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--platform=linux/amd64",
                "--entrypoint=/opt/venv/bin/python",
                IMAGE,
                "-I",
                "-c",
                PROBE,
            ],
            capture_output=True,
            check=True,
            timeout=45,
        )
        if result.stdout == b"RUNTIME_SUPPORTED=1\n" and not result.stderr:
            print(f"Pinned image runtime passed ({IMAGE}).")
            return 0
    except (OSError, subprocess.SubprocessError):
        pass
    print("Pinned image runtime evidence is unavailable or failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
