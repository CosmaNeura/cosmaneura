"""Verify the vendored runner against the hosted production checks contract."""

import runpy
import unittest

from contract import (
    BOOTSTRAP_SHA,
    G2,
    PACKAGE,
    PACKAGE_SHA,
    RUNNER_SHA,
    sha256,
    verify_package,
)


class RunnerPackageTests(unittest.TestCase):
    def test_all_bytes_manifest_bootstrap_and_sql(self):
        verify_package()
        for filename, digest in (
            ("SHA256SUMS", PACKAGE_SHA),
            ("runner.py", RUNNER_SHA),
            ("bootstrap-args.txt", BOOTSTRAP_SHA),
        ):
            with self.subTest(filename=filename):
                self.assertEqual(sha256((PACKAGE / filename).read_bytes()), digest)

    def test_g2_matches_runner_reference(self):
        reference = runpy.run_path(str(PACKAGE / "tests" / "pass_check.py"))
        self.assertEqual(G2, reference["EXPECT_G2"])
