"""Check the already-cached pinned image without network or credential inputs."""

import argparse
import re
import subprocess
import sys

from contract import IMAGE

STAGING_IMAGE = (
    r"cosmaneurastagingregistry\.azurecr\.io/"
    r"cosmaneura/cosmaneura@sha256:[0-9a-f]{64}"
)
PROBE = """
import sys
import psycopg
assert sys.executable == '/opt/venv/bin/python'
assert sys.version_info[:2] == (3, 13)
assert psycopg.__version__ == '3.2.6'
assert psycopg.pq.version() >= 170004
print('RUNTIME_SUPPORTED=1')
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Staging digest for the rehearsal")
    args = parser.parse_args(argv)
    image = IMAGE
    if args.image is not None:
        if not re.fullmatch(STAGING_IMAGE, args.image):
            parser.error("--image requires a staging registry digest")
        image = args.image
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
                image,
                "-I",
                "-c",
                PROBE,
            ],
            capture_output=True,
            check=True,
            timeout=45,
        )
        if result.stdout == b"RUNTIME_SUPPORTED=1\n" and not result.stderr:
            print(f"Pinned image runtime passed ({image}).")
            return 0
    except (OSError, subprocess.SubprocessError):
        pass
    print("Pinned image runtime evidence is unavailable or failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
