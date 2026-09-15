"""Invented archives/configuration only; no builder, Docker, account or network."""

import importlib.util
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "staging_build_inputs_fixture", ROOT / "scripts/staging_human_build_inputs.py"
)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)
CANDIDATE = "a" * 40
BINARY = b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01INVENTED_NOT_AN_EXECUTABLE"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def configuration():
    return {
        "NODE_ENV": "staging",
        "OPENAPI_JSON_PATH": "openapi.json",
        "VITE_CLERK_PUBLIC_KEY": "pk_test_invented",
        "VITE_POSTHOG_KEY": "invented-public",
        "VITE_INTAKE_CHAT_DOMAIN": "staging-chat.cosmaneura.com",
        "VITE_POSTHOG_HOST": "https://invented.invalid/",
        "VITE_PYTHON_URL": "https://staging-app.cosmaneura.com/",
        "VITE_SENTRY_DSN": "https://invented.invalid/1",
        "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED": "true",
        "VITE_SOVEREIGN_PROVIDER_URL": "https://invented.invalid/",
    }


def archive(entries, *, compressed=False, comment=CANDIDATE):
    output = io.BytesIO()
    with tarfile.open(
        fileobj=output,
        mode="w:gz" if compressed else "w",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": comment},
    ) as target:
        for name, content, kind in entries:
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name == "nixpacks" else 0o644
            member.type = kind
            if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                member.linkname = "/invented/outside"
            member.size = len(content) if kind == tarfile.REGTYPE else 0
            target.addfile(member, io.BytesIO(content) if member.size else None)
    return output.getvalue()


def source_files():
    # Only the tracked public seven-variable baseline is real, hash-pinned data.
    return [(".env", (ROOT / ".env").read_bytes(), tarfile.REGTYPE)] + [
        (name, b"invented source\n", tarfile.REGTYPE)
        for name in (
            "web/package.json",
            "web/openapi.json",
            "pyproject.toml",
            ".dockerignore",
            "web/.dockerignore",
            "web/build-environment.mjs",
            "web/scripts/build-production.mjs",
        )
    ]


class PublicInputTests(unittest.TestCase):
    def test_unicode_controls_formats_and_surrogates_refuse(self):
        for value in ("\u0085", "\u202e", "\u200d", "\ud800", "\udfff"):
            raw = encoded(configuration() | {"VITE_POSTHOG_KEY": "public" + value})
            with (
                self.subTest(codepoint=ord(value)),
                self.assertRaises(m.BuildInputRefused),
            ):
                m.public_configuration(raw, m.sha(raw))

    def test_exact_public_input_is_independent_of_ambient_credentials(self):
        raw = encoded(configuration())
        with patch.dict(os.environ, {"GH_TOKEN": "invented", "NODE_ENV": "production"}):
            self.assertEqual(m.public_configuration(raw, m.sha(raw)), configuration())

    def test_hash_raw_shape_and_size_refuse(self):
        raw = encoded(configuration())
        for value, expected in (
            (raw, "0" * 64),
            (b"", m.sha(b"")),
            (b"x" * 16385, m.sha(b"x" * 16385)),
            (b"[]", m.sha(b"[]")),
            (b"null", m.sha(b"null")),
            (b"\xff", m.sha(b"\xff")),
        ):
            with self.subTest(value=value[:10]), self.assertRaises(m.BuildInputRefused):
                m.public_configuration(value, expected)

    def test_missing_extra_duplicate_or_nonstring_field_refuses(self):
        values = [
            configuration() | {"GH_TOKEN": "invented"},
            {k: v for k, v in configuration().items() if k != "NODE_ENV"},
            configuration() | {"VITE_POSTHOG_KEY": False},
        ]
        raws = [encoded(v) for v in values] + [
            encoded(configuration())[:-1] + b',"NODE_ENV":"staging"}'
        ]
        for raw in raws:
            with self.subTest(raw=raw[:20]), self.assertRaises(m.BuildInputRefused):
                m.public_configuration(raw, m.sha(raw))

    def test_wrong_environment_paths_keys_and_controls_refuse(self):
        for key, value in (
            ("NODE_ENV", "production"),
            ("OPENAPI_JSON_PATH", "../outside"),
            ("VITE_PYTHON_URL", "https://app.cosmaneura.com/"),
            ("VITE_INTAKE_CHAT_DOMAIN", "chat.cosmaneura.com"),
            ("VITE_CLERK_PUBLIC_KEY", "pk_live_invented"),
            ("VITE_CLERK_PUBLIC_KEY", "pk_test_"),
            ("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "yes"),
            ("VITE_POSTHOG_KEY", "invented\npoison"),
            ("VITE_POSTHOG_KEY", ""),
        ):
            raw = encoded(configuration() | {key: value})
            with (
                self.subTest(key=key, value=value),
                self.assertRaises(m.BuildInputRefused),
            ):
                m.public_configuration(raw, m.sha(raw))


class NativeAssetTests(unittest.TestCase):
    def verify_fixture(self, raw, binary=BINARY):
        with patch.multiple(
            m,
            NIXPACKS_ARCHIVE_SHA256=m.sha(raw),
            NIXPACKS_ARCHIVE_BYTES=len(raw),
            NIXPACKS_BINARY_SHA256=m.sha(binary),
            NIXPACKS_BINARY_BYTES=len(binary),
        ):
            return m.native_builder_bytes(raw)

    def test_regular_arm64_fixture_is_returned_not_executed(self):
        raw = archive([("nixpacks", BINARY, tarfile.REGTYPE)], compressed=True)
        self.assertEqual(self.verify_fixture(raw), BINARY)

    def test_wrong_archive_digest_size_or_binary_digest_refuses(self):
        raw = archive([("nixpacks", BINARY, tarfile.REGTYPE)], compressed=True)
        with self.assertRaises(m.BuildInputRefused):
            m.native_builder_bytes(raw)
        with self.assertRaises(m.BuildInputRefused):
            self.verify_fixture(raw, BINARY[:-1] + b"X")

    def test_extra_path_link_or_wrong_machine_refuses(self):
        for entries in (
            [("nixpacks", BINARY, tarfile.SYMTYPE)],
            [("nixpacks", BINARY, tarfile.LNKTYPE)],
            [("../nixpacks", BINARY, tarfile.REGTYPE)],
            [("nixpacks", BINARY, tarfile.REGTYPE), ("extra", b"x", tarfile.REGTYPE)],
        ):
            with self.subTest(entries=entries), self.assertRaises(m.BuildInputRefused):
                self.verify_fixture(archive(entries, compressed=True))
        wrong = BINARY[:4] + b"\x07\x00\x00\x01" + BINARY[8:]
        with self.assertRaises(m.BuildInputRefused):
            self.verify_fixture(
                archive([("nixpacks", wrong, tarfile.REGTYPE)], compressed=True), wrong
            )


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="invented-staging-build-")
        self.addCleanup(directory.cleanup)
        self.parent = Path(directory.name).resolve()
        self.raw = archive(source_files())
        self.frontend = encoded(configuration())
        self.inputs = m.BuildInputs(
            CANDIDATE, m.sha(self.raw), m.sha(self.frontend), "2030-01-01T00:00:00Z"
        )

    def prepare(self, **changes):
        with patch.object(m, "native_builder_bytes", return_value=BINARY):
            return m.prepare_build_inputs(
                **{
                    "parent": self.parent,
                    "inputs": self.inputs,
                    "source_archive": self.raw,
                    "frontend_json": self.frontend,
                    "native_archive": b"invented",
                    **changes,
                }
            )

    def test_source_archive_commit_digest_and_time_are_bound(self):
        self.assertEqual(
            len(m.source_entries(self.raw, self.inputs)), len(source_files())
        )
        for fields in (
            {"candidate_sha": "b" * 40},
            {"source_archive_sha256": "0" * 64},
            {"candidate_sha": "master"},
            {"created_at": "2030-99-01T00:00:00Z"},
            {"created_at": "2030-01-01"},
        ):
            with self.subTest(fields=fields), self.assertRaises(m.BuildInputRefused):
                m.source_entries(self.raw, replace(self.inputs, **fields))

    def test_unsafe_source_paths_links_and_duplicates_refuse_before_writes(self):
        for extra in (
            [("../escape", b"x", tarfile.REGTYPE)],
            [("/absolute", b"x", tarfile.REGTYPE)],
            [(".git/config", b"x", tarfile.REGTYPE)],
            [("public/old", b"x", tarfile.REGTYPE)],
            [("linked", b"", tarfile.SYMTYPE)],
            [("hardlinked", b"", tarfile.LNKTYPE)],
            [("web/package.json", b"x", tarfile.REGTYPE)],
            [("parent", b"x", tarfile.REGTYPE), ("parent/file", b"x", tarfile.REGTYPE)],
        ):
            raw = archive(source_files() + extra)
            with self.subTest(extra=extra), self.assertRaises(m.BuildInputRefused):
                self.prepare(
                    source_archive=raw,
                    inputs=replace(self.inputs, source_archive_sha256=m.sha(raw)),
                )
            self.assertEqual(list(self.parent.iterdir()), [])

    def test_mac_reserved_aliases_and_nested_nixpacks_refuse_before_writes(self):
        for name in (
            ".GIT/config",
            "PUBLIC/old",
            ".NIXPACKS/build.sh",
            "web/.nixpacks/Dockerfile",
            "web/.GiT/config",
            "web/.NIXPACKS/build.sh",
        ):
            raw = archive(source_files() + [(name, b"invented", tarfile.REGTYPE)])
            with self.subTest(name=name), self.assertRaises(m.BuildInputRefused):
                self.prepare(
                    source_archive=raw,
                    inputs=replace(self.inputs, source_archive_sha256=m.sha(raw)),
                )
            self.assertEqual(list(self.parent.iterdir()), [])

    def test_mac_canonical_duplicates_and_file_parent_aliases_refuse_before_writes(
        self,
    ):
        for first, second in (
            ("README", "readme"),
            ("caf\u00e9", "cafe\u0301"),
            ("Parent", "parent/file"),
            ("CAF\u00c9", "cafe\u0301/file"),
        ):
            extras = [(name, b"invented", tarfile.REGTYPE) for name in (first, second)]
            raw = archive(source_files() + extras)
            with (
                self.subTest(names=(first, second)),
                self.assertRaises(m.BuildInputRefused),
            ):
                self.prepare(
                    source_archive=raw,
                    inputs=replace(self.inputs, source_archive_sha256=m.sha(raw)),
                )
            self.assertEqual(list(self.parent.iterdir()), [])

    def test_distinct_printable_unicode_source_and_public_values_remain_valid(self):
        entries = source_files() + [
            ("caf\u00e9.txt", b"invented", tarfile.REGTYPE),
            ("tea.txt", b"invented", tarfile.REGTYPE),
        ]
        raw = archive(entries)
        frontend = encoded(configuration() | {"VITE_POSTHOG_KEY": "public-caf\u00e9"})
        prepared = self.prepare(
            source_archive=raw,
            frontend_json=frontend,
            inputs=replace(
                self.inputs,
                source_archive_sha256=m.sha(raw),
                frontend_configuration_sha256=m.sha(frontend),
            ),
        )
        self.assertIn("caf\u00e9.txt", dict(prepared.files))
        self.assertEqual(
            dict(prepared.frontend)["VITE_POSTHOG_KEY"], "public-caf\u00e9"
        )

    def test_missing_anchor_or_changed_shared_environment_refuses(self):
        for files in (
            source_files()[1:],
            [(n, b"changed" if n == ".env" else v, k) for n, v, k in source_files()],
        ):
            raw = archive(files)
            with self.subTest(count=len(files)), self.assertRaises(m.BuildInputRefused):
                m.source_entries(
                    raw, replace(self.inputs, source_archive_sha256=m.sha(raw))
                )

    def test_owned_unique_workspace_preserves_existing_files_and_modes(self):
        sentinel = self.parent / "user-file"
        sentinel.write_bytes(b"preserve")
        first, second = self.prepare(), self.prepare()
        self.assertNotEqual(first.root, second.root)
        self.assertEqual(sentinel.read_bytes(), b"preserve")
        self.assertEqual(stat.S_IMODE(first.root.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((first.root / "bin/nixpacks").stat().st_mode), 0o500
        )
        self.assertEqual(
            (first.root / "docker-config/config.json").read_bytes(), b"{}\n"
        )
        self.assertEqual(list((first.root / "home").iterdir()), [])
        self.assertFalse((first.root / "source/public").exists())
        self.assertEqual(dict(first.files)[".env"], m.SHARED_ENV_FILE_SHA256)
        self.assertRegex(first.image, r"^cosmaneura-build-[0-9a-f]{32}:[0-9a-f]{40}$")
        self.assertRegex(
            first.extraction_container, r"^cosmaneura-assets-[0-9a-f]{32}$"
        )

    def test_unsafe_parent_and_collision_do_not_overwrite(self):
        self.parent.chmod(0o755)
        with self.assertRaises(m.BuildInputRefused):
            self.prepare()
        self.parent.chmod(0o700)
        link = self.parent / "link"
        link.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(m.BuildInputRefused):
            self.prepare(parent=link)
        with patch.object(m.secrets, "token_hex", return_value="f" * 32):
            prepared = self.prepare()
            with self.assertRaises(FileExistsError):
                self.prepare()
        self.assertEqual((prepared.root / "bin/nixpacks").read_bytes(), BINARY)

    def test_bounded_exact_argv_keeps_public_values_as_single_arguments(self):
        config = configuration() | {
            "VITE_POSTHOG_KEY": "invented $(not-executed) ; public"
        }
        raw = encoded(config)
        prepared = self.prepare(
            frontend_json=raw,
            inputs=replace(self.inputs, frontend_configuration_sha256=m.sha(raw)),
        )
        js = m.build_arguments(prepared, javascript=True)
        backend = m.build_arguments(prepared, javascript=False)
        for args in (js, backend):
            self.assertIn("--platform=linux/amd64", args)
            self.assertIn("--no-cache", args)
            self.assertNotIn("--cache-from", args)
            self.assertFalse(
                any(":latest" in value or "azurecr.io" in value for value in args)
            )
            self.assertIn("org.opencontainers.image.revision=" + CANDIDATE, args)
        self.assertIn("NODE_ENV=staging", js)
        self.assertIn("VITE_POSTHOG_KEY=" + config["VITE_POSTHOG_KEY"], js)
        self.assertIn("PYTHON_ENV=staging", backend)
        self.assertFalse(any(value.startswith("VITE_") for value in backend))
        self.assertNotIn("NODE_ENV=production", js)
        self.assertEqual(js[1:3], ("build", "web"))
        self.assertEqual(backend[1:3], ("build", "."))


if __name__ == "__main__":
    unittest.main()
