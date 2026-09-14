"""Invented identity/CLI/cache fixtures; public OS CA only as TLS parser input.

No real configured account/cache/token, CLI, network, encrypted image or data
operation. Runtime/workspace/config bindings are mocked here and tested by their
own components. Positive CA ownership below is an explicitly invented fixture.
"""

import atexit
import base64
import copy
import functools
import hashlib
import importlib.util
import json
import locale
import os
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

for module_name in (
    "staging_encrypted_workspace",
    "staging_snapshot_capture",
    "staging_snapshot_pg_io",
    "staging_snapshot_identity",
    "staging_snapshot_native_files",
    "staging_snapshot_runtime",
    "staging_snapshot_workspace",
    "staging_snapshot_configuration",
    "staging_snapshot_cli",
    "staging_snapshot_session",
):
    if module_name in sys.modules:
        continue
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parents[2] / "scripts" / (module_name + ".py"),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
session = sys.modules["staging_snapshot_session"]
pg = sys.modules["staging_snapshot_pg_io"]
native = sys.modules["staging_snapshot_native_files"]
identity = sys.modules["staging_snapshot_identity"]
workspace = sys.modules["staging_snapshot_workspace"]
FILES = None
PUBLIC_CA = b""


def isolated_tests(cls):
    for name, method in list(vars(cls).items()):
        if not name.startswith("test_"):
            continue

        @functools.wraps(method)
        def run(self, _method=method, _name=name):
            if FILES is not None:
                return _method(self)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    __file__,
                    cls.__name__ + "." + _name,
                ],
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())

        setattr(cls, name, run)
    return cls


def encoded(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


@isolated_tests
class SessionTests(unittest.TestCase):
    def start_patch(self, value):
        result = value.start()
        self.addCleanup(value.stop)
        return result

    def setUp(self):
        if FILES is None:
            return
        literal = session.expected_identity()
        self.expected = identity.ExpectedIdentity(
            literal.subscription, literal.tenant, identity.digest("invented-user")
        )
        self.account = {
            "id": literal.subscription,
            "tenantId": literal.tenant,
            "state": "Enabled",
            "environmentName": "AzureCloud",
            "user": {"type": "user", "name": "invented-user"},
        }
        self.claims = {
            "tid": literal.tenant,
            "oid": "00000000-0000-0000-0000-000000000003",
            "iss": "https://sts.windows.net/" + literal.tenant + "/",
            "aud": "https://management.azure.com/",
            "exp": int(time.time()) + 3600,
            "nbf": int(time.time()) - 30,
        }
        self.token = self.make_token()
        self.cache = {
            "Account": {
                "invented": {
                    "username": "invented-user",
                    "home_account_id": "invented.home",
                }
            },
            "AccessToken": {
                "invented": {
                    "secret": self.token,
                    "home_account_id": "invented.home",
                    "realm": literal.tenant,
                    "credential_type": "AccessToken",
                    "client_id": identity.CLI_CLIENT_ID,
                }
            },
        }
        self.responses = {
            "version": {"fixtureVersion": True},
            "cloud": {"fixtureCloud": True},
            "account": self.account,
            "token": {
                "accessToken": self.token,
                "tokenType": "Bearer",
                "subscription": literal.subscription,
                "tenant": literal.tenant,
            },
        }
        original_fingerprint = session.fingerprint

        def fingerprint(value):
            if value == {"fixtureVersion": True}:
                return (
                    "98b3412b818478b3ef731be86fd539b32628e5621342f4873ac325c87428facc"
                )
            if value == {"fixtureCloud": True}:
                return (
                    "1f30818094483f03f402801991b418f9065244acc8c871047ca46e5ce58b872f"
                )
            return original_fingerprint(value)

        self.start_patch(patch.object(session, "fingerprint", side_effect=fingerprint))
        self.start_patch(
            patch.object(session, "expected_identity", return_value=self.expected)
        )
        source = object.__new__(session.ConfiguredSource)
        source._files, source._home, source._original = (
            FILES,
            Path("/invented/not-read"),
            (),
        )
        area = workspace.BoundArea(
            object.__new__(workspace.SnapshotWorkspace),
            Path("/invented"),
            -1,
            -1,
            1,
            2,
            3,
        )
        copied = session.BoundConfiguration(
            source, area, Path("/invented/private-copy"), -1, -1, 2, 3
        )
        self.cli = session.OwnedAzureCLI(
            object.__new__(session.SnapshotRuntime), copied
        )
        self.calls, self.checks = [], []
        self.runtime_failure = False

        def cli_read(_cli, operation, *, deadline):
            self.assertEqual(pg.signal.getitimer(pg.signal.ITIMER_REAL), (0.0, 0.0))
            self.calls.append((operation, deadline))
            return copy.deepcopy(self.responses[operation])

        def cli_verify(_cli, deadline):
            self.assertGreater(pg.signal.getitimer(pg.signal.ITIMER_REAL)[0], 0)
            self.checks.append(deadline)
            if self.runtime_failure:
                raise RuntimeError("invented private detail")

        def cache_read(_source):
            self.assertGreater(pg.signal.getitimer(pg.signal.ITIMER_REAL)[0], 0)
            return json.dumps(self.cache).encode()

        self.start_patch(patch.object(session.OwnedAzureCLI, "read", cli_read))
        self.start_patch(patch.object(session.OwnedAzureCLI, "_verify", cli_verify))
        self.start_patch(
            patch.object(session.ConfiguredSource, "identity_cache", cache_read)
        )
        self.ca = native.FileObservation(PUBLIC_CA, (1,), (2,))

        def public_fixture_ca(_files):
            self.assertIs(_files, FILES)
            self.assertGreater(pg.signal.getitimer(pg.signal.ITIMER_REAL)[0], 0)
            return self.ca

        self.start_patch(patch.object(session, "fixed_ca", public_fixture_ca))

    def make_token(self):
        return (
            encoded({"alg": "invented-not-signed"})
            + "."
            + encoded(self.claims)
            + "."
            + "x" * 80
        )

    def bind(self, deadline=None):
        return session.SnapshotSession(
            self.cli, FILES, deadline=deadline or time.monotonic() + 5
        )

    def test_exact_token_original_home_link_and_shared_deadline(self):
        deadline = time.monotonic() + 5
        bound = self.bind(deadline)
        self.assertEqual(
            [name for name, _ in self.calls], ["version", "cloud", "token", "account"]
        )
        self.assertTrue(all(value == deadline for _, value in self.calls))
        self.assertTrue(all(value == deadline for value in self.checks))
        evidence = bound.verify(deadline=deadline)
        self.assertEqual(
            evidence["exact_arm_token_sha256"], identity.digest(self.token)
        )
        self.assertTrue(evidence["authenticated_same_token_read_required"])
        self.assertFalse(evidence["jwt_signature_independently_verified"])
        self.assertFalse(evidence["human_apply_authorized"])
        for private in (
            self.token,
            "invented-user",
            "invented.home",
            self.claims["oid"],
        ):
            self.assertNotIn(private, str(evidence))

    def test_token_found_only_in_copy_refuses_original_linkage(self):
        self.claims["exp"] += 1
        self.responses["token"]["accessToken"] = self.make_token()
        with self.assertRaisesRegex(
            session.SnapshotSessionRefused, "^snapshot_session_refused$"
        ):
            self.bind()

    def test_changed_version_or_cloud_refuses_before_token(self):
        for operation in ("version", "cloud"):
            saved = self.responses[operation]
            self.responses[operation] = {"changed": True}
            self.calls.clear()
            with self.assertRaises(session.SnapshotSessionRefused):
                self.bind()
            self.assertNotIn("token", [name for name, _ in self.calls])
            self.responses[operation] = saved

    def test_token_envelope_scope_and_kind_refuse(self):
        for key in ("tokenType", "subscription", "tenant", "accessToken"):
            saved = self.responses["token"][key]
            self.responses["token"][key] = None
            with self.assertRaises(session.SnapshotSessionRefused):
                self.bind()
            self.responses["token"][key] = saved

    def test_preparation_requires_full_35_minute_lifetime(self):
        self.claims["exp"] = int(time.time()) + 2100
        self.token = self.make_token()
        self.responses["token"]["accessToken"] = self.token
        self.cache["AccessToken"]["invented"]["secret"] = self.token
        with self.assertRaises(session.SnapshotSessionRefused):
            self.bind()

    def test_changed_original_cache_account_or_token_refuses(self):
        bound = self.bind()
        self.cache["Account"]["invented"]["home_account_id"] = "different"
        with self.assertRaises(session.SnapshotSessionRefused):
            bound.verify(deadline=time.monotonic() + 5)
        self.cache["Account"]["invented"]["home_account_id"] = "invented.home"
        self.account["user"]["name"] = "different"
        with self.assertRaises(session.SnapshotSessionRefused):
            bound.verify(deadline=time.monotonic() + 5)
        self.account["user"]["name"] = "invented-user"
        bound._token = "different"
        with self.assertRaises(session.SnapshotSessionRefused):
            bound.verify(deadline=time.monotonic() + 5)

    def test_runtime_or_ca_change_refuses_verify_and_tls(self):
        bound = self.bind()
        for kind in ("runtime", "ca"):
            self.runtime_failure = kind == "runtime"
            if kind == "ca":
                self.ca = native.FileObservation(PUBLIC_CA, (99,), (2,))
            for method in (bound.verify, bound.context):
                with self.assertRaisesRegex(
                    session.SnapshotSessionRefused, "^snapshot_session_refused$"
                ):
                    method(deadline=time.monotonic() + 5)

    def test_ambient_transport_refuses_before_cli_or_cache(self):
        for name in (
            "HTTP_PROXY",
            "https_proxy",
            "SSLKEYLOGFILE",
            "OPENSSL_CONF",
            "DYLD_INSERT_LIBRARIES",
            "PYTHONPATH",
        ):
            with (
                patch.dict(os.environ, {name: "invented"}),
                self.assertRaises(session.SnapshotSessionRefused),
            ):
                self.bind()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.checks, [])

    def test_deadline_not_renewed_between_cli_steps(self):
        original = session.OwnedAzureCLI.read

        def slow(cli, operation, *, deadline):
            result = original(cli, operation, deadline=deadline)
            if operation == "cloud":
                with pg.capture_deadline(deadline):
                    time.sleep(1)
            return result

        started = time.monotonic()
        with (
            patch.object(session.OwnedAzureCLI, "read", slow),
            self.assertRaises(session.SnapshotSessionRefused),
        ):
            self.bind(started + 0.08)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertNotIn("token", [name for name, _ in self.calls])

    def test_unbound_cli_or_replaced_files_refuse_before_io(self):
        with self.assertRaises(session.SnapshotSessionRefused):
            session.SnapshotSession(
                SimpleNamespace(), FILES, deadline=time.monotonic() + 5
            )
        self.assertEqual(self.calls, [])
        bound = self.bind()
        self.calls.clear()
        bound._files = SimpleNamespace()
        with self.assertRaises(session.SnapshotSessionRefused):
            bound.verify(deadline=time.monotonic() + 5)
        self.assertEqual(self.calls, [])

    def test_tls_context_uses_provided_public_bundle_with_verification(self):
        context = self.bind().context(deadline=time.monotonic() + 5)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertTrue(context.options & ssl.OP_NO_COMPRESSION)
        self.assertGreater(context.cert_store_stats()["x509_ca"], 0)

    def test_new_action_window_requires_remaining_lifetime_without_refresh(self):
        bound = self.bind()
        self.calls.clear()
        with patch.object(session.time, "time", return_value=int(time.time()) + 2000):
            with self.assertRaises(session.SnapshotSessionRefused):
                bound.verify(
                    deadline=time.monotonic() + 5, minimum_remaining_seconds=1800
                )
        self.assertEqual([name for name, _ in self.calls], ["account"])


@isolated_tests
class FixedCaTests(unittest.TestCase):
    def setUp(self):
        if FILES is None:
            return
        temporary = tempfile.TemporaryDirectory(prefix="snapshot-public-ca-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "cert.pem"
        self.path.write_bytes(PUBLIC_CA)
        self.path.chmod(0o644)
        self.inode = self.path.stat().st_ino
        self.overrides = {"st_uid": 0}  # Invented root owner, NOT native proof.
        real_fstat, real_stat = os.fstat, os.stat

        def metadata(value):
            if value.st_ino != self.inode:
                return value
            fields = {
                name: getattr(value, name)
                for name in dir(value)
                if name.startswith("st_")
            }
            fields.update(self.overrides)
            return SimpleNamespace(**fields)

        @contextmanager
        def fixture_parent(_files, path):
            self.assertEqual(path, Path("/private/etc/ssl/cert.pem"))
            with native.owned_open(
                self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            ) as descriptor:
                yield descriptor

        self.patches = [
            patch.object(native.NativeFiles, "protected_parent", fixture_parent),
            patch.object(
                session.os, "fstat", side_effect=lambda fd: metadata(real_fstat(fd))
            ),
            patch.object(
                session.os,
                "stat",
                side_effect=lambda *args, **kwargs: metadata(
                    real_stat(*args, **kwargs)
                ),
            ),
        ]
        real_sha = hashlib.sha256
        self.wrong_hash = False

        def mapped_public_pin(value):
            if value == PUBLIC_CA:
                return SimpleNamespace(
                    hexdigest=lambda: "0" * 64
                    if self.wrong_hash
                    else "9dae8d76e55cb08991f2b672d58999ea15560d910759c16b544f843bdffbb994"
                )
            return real_sha(value)

        self.patches.append(
            patch.object(session.hashlib, "sha256", side_effect=mapped_public_pin)
        )
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def read(self):
        with pg.capture_deadline(time.monotonic() + 5):
            return session.fixed_ca(FILES)

    def test_fixed_path_protected_read_and_pin(self):
        self.assertEqual(self.read().content, PUBLIC_CA)
        self.wrong_hash = True
        with self.assertRaises(session.SnapshotSessionRefused):
            self.read()

    def test_non_root_writable_linked_or_oversize_bundle_refuses(self):
        for field, value in (
            ("st_uid", 12345),
            ("st_mode", stat.S_IFREG | 0o666),
            ("st_nlink", 2),
            ("st_size", 4 * 1024**2 + 1),
        ):
            saved = dict(self.overrides)
            self.overrides[field] = value
            with (
                self.subTest(field=field),
                self.assertRaises(session.SnapshotSessionRefused),
            ):
                self.read()
            self.overrides = saved

    def test_ca_mutation_during_read_refuses(self):
        original_read = os.read
        changed = False

        def reading(fd, count):
            nonlocal changed
            value = original_read(fd, count)
            if not changed:
                changed = True
                self.path.write_bytes(PUBLIC_CA + b"\n")
            return value

        with (
            patch.object(session.os, "read", side_effect=reading),
            self.assertRaises(session.SnapshotSessionRefused),
        ):
            self.read()


if __name__ == "__main__":
    assert callable(atexit.register)
    locale.normalize("C")
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    public_path = ssl.get_default_verify_paths().cafile
    assert public_path is not None
    PUBLIC_CA = Path(public_path).read_bytes()
    FILES = native.NativeFiles()
    pg.seal_capture_process()
    unittest.main()
