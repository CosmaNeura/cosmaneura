"""Exact read-only staging ARM I/O for the future reviewed HUMAN launcher.

No CLI or firewall mutation capability. listSecrets is one documented read-only
POST against the existing app, not a resource change. Never invoke live from an
agent. The launcher must authenticate all source/dependencies/plan/handoff first.
The helper and reader below MUST be the exact pinned existing runtime classes,
not objects supplied by a plan or stubbed callbacks. Tests supply fixtures only.
"""

from __future__ import annotations

import email.utils
import hashlib
import ipaddress
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from staging_snapshot_capture import Rule
from staging_snapshot_identity import (
    ExpectedIdentity,
    bind_configured_identity,
    read_object,
)
from staging_snapshot_pg_io import (
    capture_deadline,
    credentials_from_exact_source_dsn,
    require_capture_process,
)

ACCOUNT_SHA256 = "acf2c107f43fa6950918e5f089e1b865ba56188295b88d3d72ad342d742e616a"
APP = "cosmaneura-staging-app"
REVISION = APP + "--recovery-475908dbb4c"
CONTAINER = APP + "-web"
SERVER = "cosmaneura-staging-postgres"
HOST = SERVER + ".postgres.database.azure.com"
API_APP = "2024-03-01"
API_PG = "2024-08-01"
ORIGIN = "https://management.azure.com"
SECRET_NAME = "database-url"
MAX_BODY = 1024 * 1024


class AzureReadRefused(RuntimeError):
    pass


def require(condition: object) -> None:
    if not condition:
        raise AzureReadRefused("snapshot_azure_read_refused")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def records(value: object, limit: int) -> list[dict[str, Any]]:
    require(type(value) is list)
    if not isinstance(value, list):
        raise AzureReadRefused("snapshot_azure_read_refused")
    require(len(value) <= limit and all(type(item) is dict for item in value))
    return cast(list[dict[str, Any]], value)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise AzureReadRefused("snapshot_azure_read_refused")

    def http_error_302(self, req, fp, code, msg, headers):
        fp.close()
        raise AzureReadRefused("snapshot_azure_read_refused")

    def http_error_301(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, code, msg, headers)

    def http_error_303(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, code, msg, headers)

    def http_error_307(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, code, msg, headers)

    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, code, msg, headers)


@dataclass(frozen=True)
class Observation:
    body: dict[str, Any] = field(repr=False)
    body_sha256: str | None
    response_epoch: int
    exact_token_sha256: str


def fixed_operation(operation: str) -> tuple[str, str, bytes | None]:
    """Closed source literals, never instance-, reader- or plan-selected routes."""
    require(type(operation) is str)
    prefix = (
        "https://management.azure.com/subscriptions/"
        "bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-staging"
    )
    app = prefix + "/providers/Microsoft.App/containerApps/cosmaneura-staging-app"
    server = (
        prefix
        + "/providers/Microsoft.DBforPostgreSQL/flexibleServers/cosmaneura-staging-postgres"
    )
    if operation == "app":
        return "GET", app + "?api-version=2024-03-01", None
    if operation == "revision":
        return (
            "GET",
            app
            + "/revisions/cosmaneura-staging-app--recovery-475908dbb4c?api-version=2024-03-01",
            None,
        )
    if operation == "server":
        return "GET", server + "?api-version=2024-08-01", None
    if operation == "rules":
        return "GET", server + "/firewallRules?api-version=2024-08-01", None
    if operation == "database_secret":
        return "POST", app + "/listSecrets?api-version=2024-03-01", b""
    raise AzureReadRefused("snapshot_azure_read_refused")


class SnapshotAzure:
    """Fixed endpoints; original token linked to pinned account/cache each time.

    The constructor does no network request. Its reader is prepared by the
    existing normally merged runtime before the short human interaction window.
    Every public operation fails with one content-free code.
    """

    __slots__ = ("_helper", "_reader", "_token", "_expected", "_ca_sha", "_identity")

    @property
    def app_id(self):
        return (
            fixed_operation("app")[1]
            .removeprefix("https://management.azure.com")
            .split("?", 1)[0]
        )

    @property
    def revision_id(self):
        return (
            fixed_operation("revision")[1]
            .removeprefix("https://management.azure.com")
            .split("?", 1)[0]
        )

    @property
    def server_id(self):
        return (
            fixed_operation("server")[1]
            .removeprefix("https://management.azure.com")
            .split("?", 1)[0]
        )

    def __init__(self, helper: Any, prepared_reader: Any, expected_ca_sha256: str):
        try:
            require_capture_process()
            self._initialize(helper, prepared_reader, expected_ca_sha256)
        except BaseException:
            raise AzureReadRefused("snapshot_azure_read_refused") from None

    def _initialize(self, helper: Any, prepared_reader: Any, expected_ca_sha256: str):
        self._helper = helper
        self._reader = prepared_reader
        self._token = prepared_reader._token
        self._expected = ExpectedIdentity(
            helper.SUBSCRIPTION, helper.TENANT, ACCOUNT_SHA256
        )
        require(re.fullmatch(r"[0-9a-f]{64}", expected_ca_sha256))
        self._ca_sha = expected_ca_sha256
        self._identity: dict[str, object] | None = None

    def _verify_session(self) -> dict[str, object]:
        h, r = self._helper, self._reader
        h._reject_ambient_transport_environment()
        r.refresh_account()
        r.revalidate_runtime()
        require(r._token == self._token)
        require(r.azure_tls_ca_bundle_sha256 == self._ca_sha)
        cache = h.recovery._read_protected_file(
            h._operator_paths()[1] / "msal_token_cache.json",
            label="snapshot configured-account linkage",
            required_mode=0o600,
        )
        current = bind_configured_identity(
            dict(r.account),
            self._token,
            cache,
            self._expected,
            now_epoch=int(time.time()),
            minimum_remaining_seconds=240,
        )
        stable = {
            key: current[key]
            for key in (
                "configured_username_sha256",
                "current_token_oid_sha256",
                "home_account_id_sha256",
                "exact_arm_token_sha256",
            )
        }
        if self._identity is None:
            self._identity = stable
        require(self._identity == stable)
        return current

    def _request(self, operation: str, timeout: float) -> Observation:
        require(type(timeout) in (int, float) and 0 < timeout <= 30)
        method, url, body = fixed_operation(operation)
        with capture_deadline(time.monotonic() + timeout):
            identity = self._verify_session()
            context, ca_sha = self._helper._hardened_tls_context()
            require(
                ca_sha == self._ca_sha
                and isinstance(context, ssl.SSLContext)
                and context.check_hostname
                and context.verify_mode == ssl.CERT_REQUIRED
            )
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                NoRedirect(),
                urllib.request.HTTPSHandler(context=context),
            )
            headers = {
                "Authorization": "Bearer " + self._token,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            }
            if method == "POST":
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(
                url,
                method=method,
                headers=headers,
                data=body,
            )
            # Exactly ONE attempt. No retry, pagination, redirects, alternate
            # endpoint, resource PUT/PATCH/DELETE, registration or console output.
            require(
                (request.get_method(), request.full_url, request.data)
                == fixed_operation(operation)
            )
            with opener.open(request, timeout=timeout) as response:
                require(response.status == 200 and response.geturl() == url)
                dates = response.headers.get_all("Date") or []
                kinds = response.headers.get_all("Content-Type") or []
                encodings = response.headers.get_all("Content-Encoding") or []
                lengths = response.headers.get_all("Content-Length") or []
                require(len(dates) == 1 and len(kinds) == 1)
                require(kinds[0].split(";", 1)[0].strip().lower() == "application/json")
                require(not encodings or encodings == ["identity"])
                require(
                    not lengths
                    or (
                        len(lengths) == 1
                        and lengths[0].isdigit()
                        and 0 < int(lengths[0]) <= MAX_BODY
                    )
                )
                timestamp = email.utils.parsedate_to_datetime(dates[0])
                require(timestamp.utcoffset().total_seconds() == 0)
                require(abs((datetime.now(UTC) - timestamp).total_seconds()) <= 60)
                raw = response.read(MAX_BODY + 1)
                require(0 < len(raw) <= MAX_BODY)
                require(not lengths or int(lengths[0]) == len(raw))
                value = read_object(raw, MAX_BODY)
                require("nextLink" not in value or value["nextLink"] is None)
            after = self._verify_session()
            require(
                after["exact_arm_token_sha256"] == identity["exact_arm_token_sha256"]
            )
            return Observation(
                value,
                None if operation == "database_secret" else digest(raw),
                int(timestamp.timestamp()),
                digest(self._token.encode()),
            )

    def _observe(self, operation: str, timeout: float) -> Observation:
        try:
            return self._request(operation, timeout)
        except urllib.error.HTTPError as error:
            error.close()
            raise AzureReadRefused("snapshot_azure_read_refused") from None
        except BaseException:
            raise AzureReadRefused("snapshot_azure_read_refused") from None

    def observe_server(self, timeout: float) -> dict[str, object]:
        try:
            observation = self._observe("server", timeout)
            body = observation.body
            require(body.get("id", "").casefold() == self.server_id.casefold())
            require(
                body.get("type", "").casefold()
                == "microsoft.dbforpostgresql/flexibleservers"
            )
            properties = body["properties"]
            require(
                properties["fullyQualifiedDomainName"] == HOST
                and properties["version"] == "16"
            )
            require(properties["state"] == "Ready")
            require(properties["network"]["publicNetworkAccess"] == "Enabled")
            return {
                "server_body_sha256": observation.body_sha256,
                "response_epoch": observation.response_epoch,
                "exact_token_sha256": observation.exact_token_sha256,
                "target_matches": True,
                "not_execution_authority": True,
            }
        except BaseException:
            raise AzureReadRefused("snapshot_azure_read_refused") from None

    def list_rules(self, timeout: float) -> tuple[Rule, ...]:
        try:
            body = self._observe("rules", timeout).body
            values = records(body.get("value"), 100)
            result = []
            for item in values:
                name = item["name"]
                require(
                    type(name) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name)
                )
                expected_id = self.server_id + "/firewallRules/" + name
                require(item.get("id", "").casefold() == expected_id.casefold())
                require(
                    item.get("type", "").casefold()
                    == "microsoft.dbforpostgresql/flexibleservers/firewallrules"
                )
                properties = item["properties"]
                start, end = properties["startIpAddress"], properties["endIpAddress"]
                require(type(start) is str and type(end) is str)
                first, last = ipaddress.IPv4Address(start), ipaddress.IPv4Address(end)
                require(str(first) == start and str(last) == end and first <= last)
                result.append(Rule(name, start, end))
            require(len({item.name.casefold() for item in result}) == len(result))
            return tuple(result)
        except BaseException:
            raise AzureReadRefused("snapshot_azure_read_refused") from None

    def _source_binding(
        self, app: dict[str, Any], revision: dict[str, Any], expected_image: str
    ) -> str:
        require(app.get("id", "").casefold() == self.app_id.casefold())
        require(revision.get("id", "").casefold() == self.revision_id.casefold())
        properties, revision_properties = app["properties"], revision["properties"]
        require(properties["latestReadyRevisionName"] == REVISION)
        require(
            properties["latestRevisionName"] == REVISION
            and revision_properties["active"] is True
        )
        for template in (properties["template"], revision_properties["template"]):
            containers = records(template["containers"], 1)
            require(len(containers) == 1)
            container = containers[0]
            require(
                container["name"] == CONTAINER and container["image"] == expected_image
            )
            environment = records(container["env"], 128)
            require(
                all(
                    type(item.get("name")) is str and 0 < len(item["name"]) <= 128
                    for item in environment
                )
            )
            require(len({item["name"] for item in environment}) == len(environment))
            matches = [item for item in environment if item["name"] == "DATABASE_URL"]
            require(len(matches) == 1 and matches[0].get("secretRef") == SECRET_NAME)
            require(matches[0].get("value") is None)
        return digest(
            canonical(
                {
                    "app": properties["template"],
                    "configuration": properties["configuration"],
                    "revision": revision_properties["template"],
                }
            )
        )

    def read_bound_database_credentials(self, expected_image: str, timeout: float):
        """Secret remains in memory only; no secret body or digest is published.

        Caller must bind expected_image to the exact reviewed plan; no moving
        tag is allowed. This operation does not start pg_dump or change access.
        """
        try:
            require_capture_process()
            require(
                re.fullmatch(
                    r"cosmaneurastagingregistry\.azurecr\.io/cosmaneura/cosmaneura@sha256:[0-9a-f]{64}",
                    expected_image,
                )
            )
            require(type(timeout) in (int, float) and 0 < timeout <= 120)
            deadline = time.monotonic() + timeout

            def remaining():
                value = min(30, deadline - time.monotonic())
                require(value > 0)
                return value

            app = self._observe("app", remaining()).body
            revision = self._observe("revision", remaining()).body
            before = self._source_binding(app, revision, expected_image)
            secrets_body = self._observe("database_secret", remaining()).body
            values = records(secrets_body.get("value"), 128)
            require(len({item["name"] for item in values}) == len(values))
            candidates = [item for item in values if item["name"] == SECRET_NAME]
            require(len(candidates) == 1 and not candidates[0].get("keyVaultUrl"))
            credentials = credentials_from_exact_source_dsn(candidates[0]["value"])
            del secrets_body, values, candidates
            app_after = self._observe("app", remaining()).body
            revision_after = self._observe("revision", remaining()).body
            require(
                self._source_binding(app_after, revision_after, expected_image)
                == before
            )
            require(time.monotonic() < deadline)
            return credentials, before
        except BaseException:
            raise AzureReadRefused("snapshot_azure_read_refused") from None


if __name__ == "__main__":
    raise SystemExit("snapshot_azure_requires_separately_reviewed_human_launcher")
