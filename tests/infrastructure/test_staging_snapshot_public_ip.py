"""Invented IP/approval/CA fixtures, mocked HTTP; never contact ipify or Azure."""

import atexit
import email.message
import functools
import hashlib
import importlib.util
import io
import locale
import os
import signal
import ssl
import subprocess
import sys
import time
import unittest
import urllib.error
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

for name in (
    "staging_encrypted_workspace",
    "staging_snapshot_capture",
    "staging_snapshot_identity",
    "staging_snapshot_pg_io",
    "staging_snapshot_native_files",
    "staging_snapshot_runtime",
    "staging_snapshot_workspace",
    "staging_snapshot_configuration",
    "staging_snapshot_cli",
    "staging_snapshot_session",
    "staging_snapshot_azure",
    "staging_snapshot_public_ip",
):
    if name in sys.modules:
        continue
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[2] / "scripts" / (name + ".py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
public = sys.modules["staging_snapshot_public_ip"]
pg = sys.modules["staging_snapshot_pg_io"]
native = sys.modules["staging_snapshot_native_files"]
FILES = None


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
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
                capture_output=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())

        setattr(cls, name, run)
    return cls


class Response:
    def __init__(
        self, raw=b"8.8.8.8", *, status=200, url=public.URL, headers=None, on_read=None
    ):
        self.raw, self.status, self.url = raw, status, url
        self.headers = email.message.Message()
        if headers is None:
            headers = [
                ("Content-Type", "text/plain"),
                ("Content-Length", str(len(raw))),
            ]
        for key, value in headers:
            self.headers[key] = value
        self.on_read, self.closed, self.bounds = on_read, False, []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def geturl(self):
        return self.url

    def read(self, bound):
        self.bounds.append(bound)
        if self.on_read:
            self.on_read()
        return self.raw[:bound]


@isolated_tests
class PublicIPTests(unittest.TestCase):
    def setUp(self):
        if FILES is None:
            return
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=True)
        )
        self.approval = native.FileObservation(b"invented permission", (1,), (2,))
        self.ca = native.FileObservation(b"invented CA", (3,), (4,))
        self.permission = self.stack.enter_context(
            patch.object(native.NativeFiles, "read", return_value=self.approval)
        )
        self.stack.enter_context(
            patch.object(
                public,
                "APPROVAL_SHA256",
                hashlib.sha256(self.approval.content).hexdigest(),
            )
        )
        self.ca_reader = self.stack.enter_context(
            patch.object(public, "fixed_ca", return_value=self.ca)
        )
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.options |= ssl.OP_NO_COMPRESSION
        self.context_factory = self.stack.enter_context(
            patch.object(public, "tls_context", return_value=self.context)
        )
        self.response = Response()
        self.opener = Mock()
        self.opener.open.side_effect = lambda *_args, **_kwargs: self.response
        self.builder = self.stack.enter_context(
            patch.object(
                public.urllib.request, "build_opener", return_value=self.opener
            )
        )
        self.rechecks = []
        self.adapter = public.SnapshotPublicIP(FILES, self.revalidate)

    def revalidate(self):
        self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        self.rechecks.append(time.monotonic())

    def read(self, timeout: float = 2):
        return self.adapter.current_ipv4(timeout)

    def test_exact_request_no_token_body_proxy_or_credentials(self):
        self.assertEqual(self.read(), "8.8.8.8")
        request = self.opener.open.call_args.args[0]
        self.assertEqual(
            (request.get_method(), request.full_url, request.data),
            ("GET", "https://api.ipify.org", None),
        )
        self.assertEqual(
            dict(request.header_items()),
            {"Accept": "text/plain", "Accept-encoding": "identity"},
        )
        self.assertGreater(self.opener.open.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(self.opener.open.call_args.kwargs["timeout"], 2)
        proxy, redirect, https = self.builder.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertIs(type(redirect), public.NoRedirect)
        self.assertIs(https._context, self.context)
        self.assertEqual(self.response.bounds, [17])
        self.assertTrue(self.response.closed)
        self.assertEqual(len(self.rechecks), 2)
        self.assertEqual(self.permission.call_count, 2)
        self.permission.assert_called_with(public.APPROVAL, mode=0o400, maximum=4096)
        self.assertEqual(self.ca_reader.call_count, 2)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_three_attempts_only_even_across_new_instances(self):
        for _ in range(3):
            self.adapter = public.SnapshotPublicIP(FILES, self.revalidate)
            self.assertEqual(self.read(), "8.8.8.8")
        with self.assertRaises(public.PublicIPRefused):
            public.SnapshotPublicIP(FILES, self.revalidate).current_ipv4(2)
        self.assertEqual(self.opener.open.call_count, 3)

    def test_failed_attempts_consume_limit_and_never_retry(self):
        self.opener.open.side_effect = OSError("invented provider detail")
        for _ in range(4):
            with self.assertRaisesRegex(
                public.PublicIPRefused, "^snapshot_public_ip_refused$"
            ):
                self.read()
        self.assertEqual(self.opener.open.call_count, 3)

    def test_wrong_permission_refuses_before_http(self):
        self.permission.return_value = native.FileObservation(b"changed", (1,), (2,))
        with self.assertRaises(public.PublicIPRefused):
            self.read()
        self.opener.open.assert_not_called()

    def test_changed_permission_after_response_or_between_calls_refuses(self):
        changed = native.FileObservation(self.approval.content, (9,), (2,))
        self.permission.side_effect = [self.approval, changed]
        with self.assertRaises(public.PublicIPRefused):
            self.read()
        self.permission.side_effect = None
        self.permission.return_value = changed
        with self.assertRaises(public.PublicIPRefused):
            self.read()
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertTrue(self.response.closed)

    def test_ca_drift_refuses_after_closed_response(self):
        self.ca_reader.side_effect = [
            self.ca,
            native.FileObservation(b"different", (3,), (4,)),
        ]
        with self.assertRaises(public.PublicIPRefused):
            self.read()
        self.assertTrue(self.response.closed)

    def test_ambient_proxy_or_ca_override_refuses(self):
        for name in ("https_proxy", "SSL_CERT_FILE", "DYLD_LIBRARY_PATH"):
            with patch.dict(os.environ, {name: "invented"}):
                with self.assertRaises(public.PublicIPRefused):
                    self.read()
        self.opener.open.assert_not_called()

    def test_tls_hostname_verification_and_minimum_required(self):
        self.context.check_hostname = False
        with self.assertRaises(public.PublicIPRefused):
            self.read()
        self.context.check_hostname = True
        self.context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
        with self.assertRaises(public.PublicIPRefused):
            self.read()
        self.opener.open.assert_not_called()

    def test_redirect_changed_url_and_status_refused(self):
        for response in (
            Response(status=302),
            Response(url="https://elsewhere.invalid"),
            Response(status=503),
        ):
            self.response = response
            with self.assertRaises(public.PublicIPRefused):
                self.read()
            self.assertTrue(response.closed)

    def test_redirect_handler_closes_all_redirect_statuses(self):
        for code in (301, 302, 303, 307, 308):
            stream = io.BytesIO(b"invented")
            with self.assertRaises(Exception):
                getattr(public.NoRedirect(), "http_error_" + str(code))(
                    None, stream, code, "", {}
                )
            self.assertTrue(stream.closed)

    def test_http_error_closed_and_redacted(self):
        stream = io.BytesIO(b"invented private response")
        self.opener.open.side_effect = urllib.error.HTTPError(
            public.URL, 503, "invented detail", email.message.Message(), stream
        )
        with self.assertRaisesRegex(
            public.PublicIPRefused, "^snapshot_public_ip_refused$"
        ):
            self.read()
        self.assertTrue(stream.closed)
        self.opener.open.assert_called_once()

    def test_compressed_duplicate_or_wrong_content_type_refused(self):
        for headers in (
            [("Content-Type", "text/plain"), ("Content-Encoding", "gzip")],
            [("Content-Type", "text/plain"), ("Content-Type", "text/plain")],
            [("Content-Type", "application/json")],
        ):
            self.response = Response(headers=headers)
            with self.assertRaises(public.PublicIPRefused):
                self.read()
            self.assertTrue(self.response.closed)

    def test_error_close_failure_is_also_redacted(self):
        stream = io.BytesIO(b"invented response")
        error = urllib.error.HTTPError(
            public.URL, 503, "invented detail", email.message.Message(), stream
        )
        self.opener.open.side_effect = error
        with patch.object(error, "close", side_effect=OSError("invented close detail")):
            with self.assertRaisesRegex(
                public.PublicIPRefused, "^snapshot_public_ip_refused$"
            ):
                self.read()
        stream.close()

    def test_bad_declared_length_and_oversized_body_refused(self):
        for length in ("17", "0", "9"):
            self.response = Response(
                headers=[("Content-Type", "text/plain"), ("Content-Length", length)]
            )
            with self.assertRaises(public.PublicIPRefused):
                self.read()

    def test_unlengthened_oversize_and_empty_response_refused(self):
        for raw in (b"8.8.8.8" + b" " * 20, b""):
            self.response = Response(raw, headers=[("Content-Type", "text/plain")])
            with self.assertRaises(public.PublicIPRefused):
                self.read()
            self.assertEqual(self.response.bounds, [17])

    def test_noncanonical_private_ipv6_multicast_or_reserved_refused(self):
        for raw in (
            b"127.0.0.1",
            b"10.0.0.1",
            b"100.64.0.1",
            b"192.0.2.1",
            b"224.0.0.1",
            b"240.0.0.1",
            b"0.0.0.0",
            b"::1",
            b"008.8.8.8",
            b"8.8.8.8 ",
            b"8.8.8.8\n\n",
            b"\xff",
        ):
            with self.subTest(raw=raw), self.assertRaises(Exception):
                public.canonical_ipv4(raw)
        self.assertEqual(public.canonical_ipv4(b"8.8.8.8\n"), "8.8.8.8")
        self.assertEqual(public.canonical_ipv4(b"1.1.1.1"), "1.1.1.1")

    def test_invalid_timeout_never_opens_network(self):
        for value in (True, 0, -1, 16, float("nan"), float("inf")):
            with self.assertRaises(public.PublicIPRefused):
                self.read(value)
        self.opener.open.assert_not_called()

    def test_original_deadline_covers_read_and_cannot_be_swallowed(self):
        def slow():
            try:
                time.sleep(0.1)
            except pg.PostgreSQLRefused:
                pass

        self.response.on_read = slow
        with self.assertRaises(public.PublicIPRefused):
            self.read(0.02)
        self.assertTrue(self.response.closed)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_existing_timer_not_reset(self):
        with pg.capture_deadline(time.monotonic() + 2):
            before = signal.getitimer(signal.ITIMER_REAL)[0]
            with self.assertRaises(public.PublicIPRefused):
                self.read()
            after = signal.getitimer(signal.ITIMER_REAL)[0]
            self.assertGreater(after, 0)
            self.assertLessEqual(after, before)
        self.opener.open.assert_not_called()

    def test_runtime_drift_refuses_before_http_and_after_read(self):
        self.adapter = public.SnapshotPublicIP(
            FILES, Mock(side_effect=RuntimeError("invented drift"))
        )
        with self.assertRaises(public.PublicIPRefused):
            self.read()
        self.opener.open.assert_not_called()
        callback = Mock(side_effect=[None, RuntimeError("invented drift")])
        self.adapter = public.SnapshotPublicIP(FILES, callback)
        with self.assertRaises(public.PublicIPRefused):
            self.read()
        self.assertTrue(self.response.closed)


if __name__ == "__main__":
    assert callable(atexit.register)
    locale.normalize("C")
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    FILES = native.NativeFiles()
    pg.seal_capture_process()
    unittest.main()
