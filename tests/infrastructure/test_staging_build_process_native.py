"""Two bounded native child checks; no Docker, Nixpacks, network or credentials."""

import importlib
import json
import os
import resource
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    process = importlib.import_module("staging_human_build_process")

CHILD = """
import json, os
print(json.dumps({"cwd": os.getcwd(), "environment": dict(os.environ),
                  "pid": os.getpid(), "group": os.getpgrp()}), flush=True)
os.write(2, b"invented-stderr-marker\\n")
"""


@unittest.skipUnless(sys.platform == "darwin", "reviewed Darwin process lifecycle")
class NativeProcessTests(unittest.TestCase):
    def test_defer_control_signals_blocks_and_restores_supported_set(self):
        before = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        self.assertFalse(process.CONTROL_SIGNALS & before)
        with process.defer_control_signals():
            inside = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            self.assertTrue(process.CONTROL_SIGNALS <= inside)
            self.assertEqual(
                process.CONTROL_SIGNALS,
                frozenset(
                    {signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGALRM}
                ),
            )
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), before)

    def check_stream(self, merged):
        # Only this test process and its own descendants; no host configuration.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        with tempfile.TemporaryDirectory(prefix="cosmaneura-build-child-") as root:
            cwd = Path(root).resolve()
            cwd.chmod(0o700)
            environment = {
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C",
                "BUILD_TEST": "invented",
            }
            chunks, checks = [], []
            with process.build_deadline(10) as (deadline, tick):

                def validate():
                    tick()
                    checks.append(True)

                owner = process.BuildProcesses(deadline, validate)
                with patch.dict(os.environ, {"DO_NOT_INHERIT": "invented"}):
                    size = owner.run(
                        [sys.executable, "-I", "-S", "-c", CHILD],
                        environment,
                        4096,
                        chunks.append,
                        cwd=cwd,
                        merge_stderr=merged,
                    )
                raw = b"".join(chunks)
                self.assertEqual(size, len(raw))
                lines = raw.splitlines()
                child = json.loads(lines[0])
                self.assertEqual(child["cwd"], str(cwd))
                # Darwin CoreFoundation adds this UID/encoding value during
                # Python startup; it is not an inherited build credential.
                expected = dict(environment)
                expected["__CF_USER_TEXT_ENCODING"] = f"0x{os.getuid():X}:0x0:0x0"
                self.assertEqual(child["environment"], expected)
                self.assertEqual(child["pid"], child["group"])
                self.assertNotEqual(child["pid"], os.getpid())
                self.assertEqual(
                    lines[1:], [b"invented-stderr-marker"] if merged else []
                )
                self.assertIsNone(owner.child)
                self.assertFalse(owner.cleanup_failed)
                self.assertTrue(checks)

    def test_owned_cwd_exact_environment_and_default_stderr_discard(self):
        self.check_stream(False)

    def test_owned_cwd_exact_environment_and_merged_stderr(self):
        self.check_stream(True)


if __name__ == "__main__":
    unittest.main()
