"""Invented ARM/identity fixtures only. All CLI, cache and HTTP I/O is mocked."""

import base64
import copy
import email.message
import email.utils
import functools
import importlib.util
import io
import json
import locale
import os
import ssl
import subprocess
import sys
import time
import unittest
import urllib.error
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

for module_name in (
    "staging_snapshot_capture",
    "staging_snapshot_identity",
    "staging_snapshot_pg_io",
    "staging_snapshot_azure",
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
azure = sys.modules["staging_snapshot_azure"]
pg = sys.modules["staging_snapshot_pg_io"]
CLI_CLIENT_ID = sys.modules["staging_snapshot_identity"].CLI_CLIENT_ID
digest = sys.modules["staging_snapshot_identity"].digest


def dedicated_process_tests(cls):
    for name, method in list(vars(cls).items()):
        if not name.startswith("test_"):
            continue

        @functools.wraps(method)
        def run(self, _method=method, _name=name):
            if pg._capture_process_pid == os.getpid():
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


SUB = str(uuid.UUID(int=1))
TENANT = str(uuid.UUID(int=2))
OID = str(uuid.UUID(int=3))
CA_SHA = "a" * 64
IMAGE = "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:" + "b" * 64
DSN = (
    "postgresql://invented-user:invented-password@"
    + azure.HOST
    + ":5432/staging?sslmode=require"
)


def encoded(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


class Response:
    def __init__(
        self,
        body=None,
        *,
        raw=None,
        status=200,
        headers=None,
        changed_url=None,
        on_read=None,
        delay=0,
    ):
        self.raw = json.dumps(body).encode() if raw is None else raw
        self.status = status
        self.headers = email.message.Message()
        for name, value in headers or [
            ("Date", email.utils.format_datetime(datetime.now(UTC), usegmt=True)),
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(self.raw))),
        ]:
            self.headers[name] = value
        self.changed_url = changed_url
        self.url = None
        self.on_read = on_read
        self.delay = delay
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def geturl(self):
        return self.changed_url or self.url

    def read(self, limit):
        if self.on_read:
            self.on_read()
        if self.delay:
            time.sleep(self.delay)
        return self.raw[:limit]


class Opener:
    def __init__(self):
        self.requests = []
        self.responses = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        response.url = request.full_url
        return response


@dedicated_process_tests
class AzureTests(unittest.TestCase):
    def setUp(self):
        if pg._capture_process_pid != os.getpid():
            return  # The wrapper delegates both fixture setup and test to its child.
        claims = {
            "tid": TENANT,
            "oid": OID,
            "iss": f"https://sts.windows.net/{TENANT}/",
            "aud": "https://management.azure.com/",
            "exp": int(time.time()) + 3600,
            "nbf": int(time.time()) - 30,
        }
        self.token = (
            encoded({"alg": "invented"}) + "." + encoded(claims) + "." + "x" * 80
        )
        self.cache = {
            "Account": {
                "fixture": {
                    "username": "invented-user",
                    "home_account_id": "invented.home",
                }
            },
            "AccessToken": {
                "fixture": {
                    "secret": self.token,
                    "realm": TENANT,
                    "home_account_id": "invented.home",
                    "client_id": CLI_CLIENT_ID,
                    "credential_type": "AccessToken",
                }
            },
        }
        self.reader = SimpleNamespace(
            _token=self.token,
            azure_tls_ca_bundle_sha256=CA_SHA,
            account={
                "id": SUB,
                "tenantId": TENANT,
                "environmentName": "AzureCloud",
                "state": "Enabled",
                "user": {"type": "user", "name": "invented-user"},
            },
            refresh_account=Mock(),
            revalidate_runtime=Mock(),
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

        def read_cache(path, *, label, required_mode):
            assert path == Path("/invented/cache/msal_token_cache.json")
            assert required_mode == 0o600
            return json.dumps(self.cache).encode()

        self.helper = SimpleNamespace(
            SUBSCRIPTION=SUB,
            TENANT=TENANT,
            _reject_ambient_transport_environment=Mock(),
            _hardened_tls_context=Mock(return_value=(context, CA_SHA)),
            _operator_paths=lambda: (
                Path("/invented/user"),
                Path("/invented/cache"),
                "binding",
            ),
            recovery=SimpleNamespace(_read_protected_file=read_cache),
        )
        self.opener = Opener()
        self.account_patch = patch.object(
            azure, "ACCOUNT_SHA256", digest("invented-user")
        )
        self.account_patch.start()
        self.addCleanup(self.account_patch.stop)
        self.opener_patch = patch.object(
            azure.urllib.request, "build_opener", return_value=self.opener
        )
        self.opener_patch.start()
        self.addCleanup(self.opener_patch.stop)
        self.client = azure.SnapshotAzure(self.helper, self.reader, CA_SHA)

    def server(self):
        return {
            "id": self.client.server_id,
            "type": "Microsoft.DBforPostgreSQL/flexibleServers",
            "properties": {
                "fullyQualifiedDomainName": azure.HOST,
                "version": "16",
                "state": "Ready",
                "network": {"publicNetworkAccess": "Enabled"},
            },
        }

    def rule(self, name="existing", start="0.0.0.0", end="0.0.0.0"):
        return {
            "id": self.client.server_id + "/firewallRules/" + name,
            "name": name,
            "type": "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules",
            "properties": {"startIpAddress": start, "endIpAddress": end},
        }

    def source(self):
        template = {
            "containers": [
                {
                    "name": azure.CONTAINER,
                    "image": IMAGE,
                    "env": [{"name": "DATABASE_URL", "secretRef": "database-url"}],
                }
            ]
        }
        app = {
            "id": self.client.app_id,
            "properties": {
                "latestReadyRevisionName": azure.REVISION,
                "latestRevisionName": azure.REVISION,
                "template": copy.deepcopy(template),
                "configuration": {"secrets": [{"name": "database-url"}]},
            },
        }
        revision = {
            "id": self.client.revision_id,
            "properties": {"active": True, "template": copy.deepcopy(template)},
        }
        return app, revision

    def queue_source(self, secret=None, *, app_after=None, revision_after=None):
        app, revision = self.source()
        secret = (
            secret if secret is not None else {"name": "database-url", "value": DSN}
        )
        self.opener.responses = [
            Response(app),
            Response(revision),
            Response({"value": [secret]}),
            Response(app_after or app),
            Response(revision_after or revision),
        ]

    def test_same_token_read_authenticates_and_revalidates_both_sides(self):
        self.opener.responses = [Response(self.server())]
        result = self.client.observe_server(5)
        self.assertTrue(result["target_matches"])
        self.assertEqual(result["exact_token_sha256"], digest(self.token))
        self.reader.refresh_account.assert_called()
        self.assertEqual(self.reader.refresh_account.call_count, 2)
        self.assertEqual(self.reader.revalidate_runtime.call_count, 2)
        request = self.opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.token)
        self.assertTrue(request.full_url.endswith("?api-version=2024-08-01"))
        self.assertNotIn(self.token, str(result))

    def test_core_limit_refuses_before_reader_or_secret_access(self):
        with (
            patch.object(pg.resource, "getrlimit", return_value=(0, 1)),
            patch.object(azure.SnapshotAzure, "_initialize") as initialize,
        ):
            with self.assertRaises(azure.AzureReadRefused):
                azure.SnapshotAzure(self.helper, self.reader, CA_SHA)
            initialize.assert_not_called()
            with self.assertRaises(azure.AzureReadRefused):
                self.client.read_bound_database_credentials(IMAGE, 15)
        self.assertEqual(self.opener.requests, [])
        self.reader.refresh_account.assert_not_called()

    def test_wrong_server_identity_host_version_or_state_refuses(self):
        for key, value in (
            ("fullyQualifiedDomainName", "other.invalid"),
            ("version", "17"),
            ("state", "Stopped"),
            ("network", {"publicNetworkAccess": "Disabled"}),
        ):
            body = self.server()
            body["properties"][key] = value
            self.opener.responses = [Response(body)]
            with self.assertRaises(azure.AzureReadRefused):
                self.client.observe_server(5)
        body = self.server()
        body["id"] = body["id"].replace("cosmaneura-staging", "foreign")
        self.opener.responses = [Response(body)]
        with self.assertRaises(azure.AzureReadRefused):
            self.client.observe_server(5)

    def test_rule_inventory_has_exact_resource_ids_and_preserves_baseline(self):
        self.opener.responses = [
            Response({"value": [self.rule(), self.rule("task", "8.8.8.8", "8.8.8.8")]})
        ]
        rules = self.client.list_rules(5)
        self.assertEqual(
            [(r.name, r.start, r.end) for r in rules],
            [("existing", "0.0.0.0", "0.0.0.0"), ("task", "8.8.8.8", "8.8.8.8")],
        )

    def test_rules_reject_case_collisions_bad_ids_ranges_and_unknown_pages(self):
        wrong_id = self.rule()
        wrong_id["id"] += "/foreign"
        for body in (
            {"value": [self.rule(), self.rule("EXISTING")]},
            {"value": [wrong_id]},
            {"value": [self.rule(start="::1", end="::1")]},
            {"value": [self.rule(start="8.8.8.9", end="8.8.8.8")]},
            {"value": [], "nextLink": "https://other.invalid/"},
            {"value": [None]},
        ):
            self.opener.responses = [Response(body)]
            with self.assertRaises(azure.AzureReadRefused):
                self.client.list_rules(5)

    def test_only_fixed_gets_and_one_readonly_post_are_possible(self):
        routes = [
            azure.fixed_operation(value)
            for value in ("app", "revision", "server", "rules", "database_secret")
        ]
        self.assertEqual([method for method, _, _ in routes].count("POST"), 1)
        self.assertEqual(
            [path for method, path, _ in routes if method == "POST"],
            [
                "https://management.azure.com"
                + self.client.app_id
                + "/listSecrets?api-version=2024-03-01"
            ],
        )
        for action in ("create_rule", "delete_rule", "apply", "register", "foreign"):
            with self.assertRaises(azure.AzureReadRefused):
                self.client._observe(action, 5)
        self.assertEqual(self.opener.requests, [])

    def test_route_like_instance_state_cannot_enable_authenticated_mutations(self):
        for name, value in (
            ("_routes", {"rules": ("DELETE", self.client.server_id, "2024-08-01")}),
            ("routes", {"app": ("PUT", self.client.app_id, "2024-03-01")}),
            ("app_id", self.client.server_id + "/firewallRules/foreign"),
            ("server_id", self.client.app_id + "/restart"),
            ("revision_id", self.client.app_id + "/delete"),
        ):
            with self.assertRaises(AttributeError):
                setattr(self.client, name, value)
        for action in (
            "PUT",
            "PATCH",
            "DELETE",
            "restart",
            "listSecrets",
            "rules/delete",
        ):
            with self.assertRaises(azure.AzureReadRefused):
                self.client._observe(action, 5)
        self.assertEqual(self.opener.requests, [])

    def test_preopen_gate_rejects_method_url_or_post_body_tampering(self):
        original = azure.urllib.request.Request
        for field, value in (
            ("method", "DELETE"),
            ("full_url", "https://management.azure.com/foreign"),
            ("data", b"mutating-body"),
        ):

            def changed(*args, **kwargs):
                request = original(*args, **kwargs)
                setattr(request, field, value)
                return request

            with patch.object(azure.urllib.request, "Request", side_effect=changed):
                with self.assertRaises(azure.AzureReadRefused):
                    self.client._observe("database_secret", 5)
        self.assertEqual(self.opener.requests, [])

    def test_network_failure_is_single_attempt_and_error_text_is_redacted(self):
        self.opener.responses = [
            urllib.error.URLError("invented private credential detail")
        ]
        with self.assertRaisesRegex(
            azure.AzureReadRefused, "^snapshot_azure_read_refused$"
        ):
            self.client.observe_server(5)
        self.assertEqual(len(self.opener.requests), 1)

    def test_http_failure_body_is_closed_without_reading_or_retry(self):
        stream = io.BytesIO(b"invented private body")
        failure = urllib.error.HTTPError(
            "https://management.azure.com",
            403,
            "private",
            email.message.Message(),
            stream,
        )
        self.opener.responses = [failure]
        with self.assertRaises(azure.AzureReadRefused):
            self.client.observe_server(5)
        self.assertTrue(stream.closed)
        self.assertEqual(len(self.opener.requests), 1)

    def test_redirects_are_closed_and_never_followed(self):
        handler = azure.NoRedirect()
        for code in (301, 302, 303, 307, 308):
            stream = io.BytesIO(b"invented redirect body")
            with self.assertRaises(azure.AzureReadRefused):
                getattr(handler, "http_error_" + str(code))(
                    None, stream, code, "redirect", {}
                )
            self.assertTrue(stream.closed)
        self.opener.responses = [
            Response(self.server(), changed_url="https://other.invalid/")
        ]
        with self.assertRaises(azure.AzureReadRefused):
            self.client.observe_server(5)

    def test_headers_dates_encodings_lengths_and_duplicate_json_refuse(self):
        date = email.utils.format_datetime(datetime.now(UTC), usegmt=True)
        old = email.utils.format_datetime(
            datetime.now(UTC) - timedelta(minutes=5), usegmt=True
        )
        variants = [
            [("Date", old), ("Content-Type", "application/json")],
            [("Date", date), ("Date", date), ("Content-Type", "application/json")],
            [("Date", date), ("Content-Type", "text/html")],
            [
                ("Date", date),
                ("Content-Type", "application/json"),
                ("Content-Encoding", "gzip"),
            ],
            [
                ("Date", date),
                ("Content-Type", "application/json"),
                ("Content-Length", "1"),
            ],
        ]
        for headers in variants:
            self.opener.responses = [Response(self.server(), headers=headers)]
            with self.assertRaises(azure.AzureReadRefused):
                self.client.observe_server(5)
        self.opener.responses = [Response(raw=b'{"id":"one","id":"two"}')]
        with self.assertRaises(azure.AzureReadRefused):
            self.client.observe_server(5)

    def test_tls_ca_and_context_changes_refuse_before_http(self):
        self.helper._hardened_tls_context.return_value = (
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            "b" * 64,
        )
        with self.assertRaises(azure.AzureReadRefused):
            self.client.observe_server(5)
        self.assertEqual(self.opener.requests, [])

    def test_configured_user_or_runtime_failure_refuses_before_http(self):
        self.reader.account["user"]["name"] = "different"
        with self.assertRaises(azure.AzureReadRefused):
            self.client.observe_server(5)
        self.assertEqual(self.opener.requests, [])

    def test_token_change_during_response_refuses_acceptance(self):
        self.opener.responses = [
            Response(
                self.server(),
                on_read=lambda: setattr(self.reader, "_token", "different"),
            )
        ]
        with self.assertRaises(azure.AzureReadRefused):
            self.client.observe_server(5)

    def test_slow_body_is_bounded_and_response_closed(self):
        response = Response(self.server(), delay=2)
        self.opener.responses = [response]
        started = time.monotonic()
        with self.assertRaises(azure.AzureReadRefused):
            self.client.observe_server(0.04)
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(response.closed)

    def test_credential_read_binds_revision_image_reference_and_before_after_config(
        self,
    ):
        self.queue_source()
        credentials, binding = self.client.read_bound_database_credentials(IMAGE, 15)
        self.assertEqual(credentials.username, "invented-user")
        self.assertEqual(len(binding), 64)
        self.assertNotIn("invented-password", repr(credentials))
        self.assertEqual(
            [r.get_method() for r in self.opener.requests],
            ["GET", "GET", "POST", "GET", "GET"],
        )
        post = self.opener.requests[2]
        self.assertEqual(post.data, b"")
        self.assertEqual(post.get_header("Content-type"), "application/json")
        self.assertTrue(post.full_url.endswith("/listSecrets?api-version=2024-03-01"))

    def test_secret_observation_has_no_printable_body_or_secret_digest(self):
        self.opener.responses = [
            Response({"value": [{"name": "database-url", "value": DSN}]})
        ]
        value = self.client._observe("database_secret", 5)
        self.assertIsNone(value.body_sha256)
        self.assertNotIn("invented-password", repr(value))

    def test_external_secret_wrong_db_or_missing_value_refuses(self):
        for secret in (
            {
                "name": "database-url",
                "value": DSN,
                "keyVaultUrl": "https://invented.invalid/secret",
            },
            {"name": "database-url", "value": DSN.replace(azure.HOST, "other.invalid")},
            {"name": "database-url"},
            {"name": "wrong", "value": DSN},
        ):
            self.queue_source(secret)
            with self.assertRaises(azure.AzureReadRefused):
                self.client.read_bound_database_credentials(IMAGE, 15)

    def test_source_changes_during_credential_read_refuse(self):
        app, _revision = self.source()
        app["properties"]["configuration"]["changed"] = True
        self.queue_source(app_after=app)
        with self.assertRaises(azure.AzureReadRefused):
            self.client.read_bound_database_credentials(IMAGE, 15)

    def test_wrong_image_inactive_revision_or_inline_dsn_refuse_before_secret_read(
        self,
    ):
        for change in ("image", "active", "inline"):
            app, revision = self.source()
            if change == "active":
                revision["properties"]["active"] = False
            elif change == "image":
                app["properties"]["template"]["containers"][0]["image"] = (
                    "unreviewed:tag"
                )
            else:
                app["properties"]["template"]["containers"][0]["env"][0]["value"] = DSN
            self.opener.responses = [Response(app), Response(revision)]
            with self.assertRaises(azure.AzureReadRefused):
                self.client.read_bound_database_credentials(IMAGE, 15)
        self.assertTrue(all(r.get_method() == "GET" for r in self.opener.requests))


if __name__ == "__main__":
    locale.normalize("C")  # Preload gettext's lazy parser dependency before sealing.
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    pg.seal_capture_process()
    unittest.main()
