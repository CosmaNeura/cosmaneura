"""Verify the vendored runner before the hosted workflow layers are added."""

import hashlib
import unittest
from pathlib import Path

import runner

PACKAGE = Path(__file__).resolve().parent
PACKAGE_SHA = "794850f6ae7ec201bbc7ec9abba7cbc1da7a72fa263a00f8d77356c50bb14260"
RUNNER_SHA = "b940747c7ed2fdd0d414dca664c8d642adae0f92539fe2ae28d28c72f59a9b59"
BOOTSTRAP_SHA = "b722cedc815e259920c8cd6f2cb5f891369699183e853612bc95567d83d79a67"


class RunnerPackageTests(unittest.TestCase):
    def test_all_bytes_manifest_bootstrap_and_sql(self):
        manifest = (PACKAGE / "SHA256SUMS").read_bytes()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), PACKAGE_SHA)
        for line in manifest.decode().splitlines():
            digest, path = line.split("  ./", 1)
            with self.subTest(path=path):
                data = (PACKAGE / path).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)

        self.assertEqual(
            hashlib.sha256((PACKAGE / "runner.py").read_bytes()).hexdigest(),
            RUNNER_SHA,
        )
        bootstrap = (PACKAGE / "bootstrap-args.txt").read_bytes()
        self.assertEqual(hashlib.sha256(bootstrap).hexdigest(), BOOTSTRAP_SHA)
        self.assertIn(RUNNER_SHA.encode(), bootstrap)
        self.assertEqual(
            (PACKAGE / "candidate-access-counts.sql").read_bytes(),
            runner.ACCESS_SQL.encode(),
        )
        runner.self_check()
