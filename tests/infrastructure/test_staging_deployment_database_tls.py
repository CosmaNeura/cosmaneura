from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "staging_deployment_database_tls.py"
SPEC = importlib.util.spec_from_file_location("staging_deployment_database_tls", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("test_module_unavailable")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TARGET = MODULE.STAGING_HOST
CA = MODULE.LINUX_CA_BUNDLE
BASE = f"postgresql://synthetic:never-log-test-password@{TARGET}:5432/staging"


class StagingDatabaseTlsTests(unittest.TestCase):
    def test_require_is_upgraded_with_explicit_ca(self) -> None:
        actual = MODULE.normalize_staging_deployment_database_url(
            BASE + "?sslmode=require"
        )
        self.assertEqual(
            parse_qs(urlsplit(actual).query),
            {"sslmode": ["verify-full"], "sslrootcert": [CA]},
        )
        self.assertEqual(urlsplit(actual).netloc, urlsplit(BASE).netloc)

    def test_verified_connection_is_idempotent(self) -> None:
        once = MODULE.normalize_staging_deployment_database_url(
            BASE + "?sslmode=verify-full"
        )
        self.assertEqual(MODULE.normalize_staging_deployment_database_url(once), once)

    def test_encoded_credentials_are_preserved(self) -> None:
        value = BASE.replace(
            "never-log-test-password", "synthetic%40%3A%2F%3F%23%25%20"
        )
        actual = MODULE.normalize_staging_deployment_database_url(
            value + "?sslmode=require"
        )
        self.assertEqual(urlsplit(actual).netloc, urlsplit(value).netloc)

    def test_psycopg_scheme_is_preserved(self) -> None:
        value = BASE.replace("postgresql:", "postgresql+psycopg:") + "?sslmode=require"
        self.assertTrue(
            MODULE.normalize_staging_deployment_database_url(value).startswith(
                "postgresql+psycopg:"
            )
        )

    def test_wrong_targets_and_malformed_urls_refuse(self) -> None:
        values = [
            BASE.replace(TARGET, "production.invalid"),
            BASE.replace("/staging", "/production"),
            BASE.replace(":5432", ":5433"),
            BASE.replace(":5432", ""),
            BASE.replace(":5432", ":bad"),
            BASE.replace(TARGET, TARGET + ".evil.invalid"),
            BASE.replace("postgresql:", "https:"),
            BASE.replace("synthetic:", ":"),
            BASE.replace("never-log-test-password", ""),
            BASE.replace("/staging", "/%73taging"),
        ]
        for value in values:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "^staging_database_tls_contract$"),
            ):
                MODULE.normalize_staging_deployment_database_url(
                    value + "?sslmode=require"
                )

    def test_ambiguous_or_weaker_tls_options_refuse(self) -> None:
        for suffix in (
            "",
            "?sslmode=disable",
            "?sslmode=prefer",
            "?sslmode=verify-ca",
            "?sslmode=",
            "?sslmode=require&sslmode=verify-full",
            "?sslmode=require&host=production.invalid",
            "?sslmode=require&options=unsafe",
            "?sslmode=require&sslrootcert=system",
            "?sslmode=require&sslrootcert=/tmp/untrusted.pem",
            "?sslmode=require#fragment",
            "?sslmode=require&sslrootcert=",
            "?sslmode=require&broken",
        ):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                MODULE.normalize_staging_deployment_database_url(BASE + suffix)

    def test_control_characters_and_large_input_refuse(self) -> None:
        for value in (
            BASE + "?sslmode=require\n",
            BASE + "?sslmode=require\x00",
            BASE.replace("synthetic:", "synthetic%00:"),
            BASE.replace("never-log-test-password", "synthetic%0A"),
            "x" * 8193,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.normalize_staging_deployment_database_url(value)

    def test_cli_failure_never_echoes_connection_or_password(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            env={"DATABASE_URL": BASE},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "staging_database_tls_contract\n")

    def test_unicode_controls_and_separators_refuse_raw_and_encoded(self) -> None:
        for character in ("\u0080", "\u0085", "\u009f", "\u2028", "\u2029", "\u202e"):
            for representation in (character, quote(character, safe="")):
                for original in ("synthetic:", "never-log-test-password"):
                    replacement = representation + (
                        ":" if original.endswith(":") else ""
                    )
                    value = BASE.replace(original, replacement) + "?sslmode=require"
                    with (
                        self.subTest(character=ascii(character)),
                        self.assertRaises(ValueError),
                    ):
                        MODULE.normalize_staging_deployment_database_url(value)
        for value in (" " + BASE, BASE + " ", BASE.replace("synthetic:", "\ud800:")):
            with self.assertRaises(ValueError):
                MODULE.normalize_staging_deployment_database_url(
                    value + "?sslmode=require"
                )

    def test_malformed_percent_escapes_and_invalid_utf8_refuse(self) -> None:
        for malformed in ("%", "%A", "%GG", "%0G", "%FF", "%C3", "%ED%A0%80"):
            for original in ("synthetic:", "never-log-test-password"):
                replacement = malformed + (":" if original.endswith(":") else "")
                value = BASE.replace(original, replacement) + "?sslmode=require"
                with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                    MODULE.normalize_staging_deployment_database_url(value)

    def test_unicode_and_escape_cli_refusals_remain_constant(self) -> None:
        for credential in ("\u0085", "%C2%85", "%E2%80%A8", "%", "%A", "%GG", "%FF"):
            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                env={
                    "DATABASE_URL": BASE.replace("never-log-test-password", credential)
                    + "?sslmode=require"
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                (result.returncode, result.stdout, result.stderr),
                (2, "", "staging_database_tls_contract\n"),
            )

    def test_valid_utf8_and_literal_encoded_percent_are_preserved(self) -> None:
        value = (
            BASE.replace("never-log-test-password", "synthetic%C3%A9%25GG%20")
            + "?sslmode=require"
        )
        actual = MODULE.normalize_staging_deployment_database_url(value)
        self.assertEqual(urlsplit(actual).netloc, urlsplit(value).netloc)

    def test_cli_success_is_only_the_captured_value(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            env={"DATABASE_URL": BASE + "?sslmode=require"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            parse_qs(urlsplit(result.stdout.strip()).query)["sslmode"], ["verify-full"]
        )

    def test_staging_template_captures_failure_and_production_is_unchanged(
        self,
    ) -> None:
        staging = (ROOT / ".env.staging.backend").read_text()
        self.assertIn(
            "if ! DATABASE_URL=$(python scripts/staging_deployment_database_tls.py); then\n\treturn 1\nfi\nexport DATABASE_URL",
            staging,
        )
        self.assertGreater(
            staging.index("if ! DATABASE_URL="), staging.index("\nEOF\n")
        )
        for name in (
            ".env.production.backend",
            ".env.production.frontend",
            ".env.shared",
            ".env.test",
        ):
            self.assertNotIn(
                "staging_deployment_database_tls", (ROOT / name).read_text()
            )


if __name__ == "__main__":
    unittest.main()
