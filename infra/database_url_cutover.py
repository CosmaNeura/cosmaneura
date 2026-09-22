#!/usr/bin/env python3
"""Cut Container Apps over to a versioned application DATABASE_URL secret.

Default mode is a read-only plan.  Mutating modes require an explicit flag and
subscription/resource-group confirmations.  This operator accepts database
URLs only via an environment variable or stdin and sends them directly to ARM,
never through an ``az`` command argument.  The separate source-controlled
phase-A path is documented as storing inline values in protected OpenTofu state.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Sequence

ARM_API_VERSION = "2025-07-01"
ARM_ORIGIN = "https://management.azure.com"
PRODUCTION_DATABASE_HOST = "cosmaneura-prod-postgres.postgres.database.azure.com"
PRODUCTION_DATABASE_NAME = "production"
PRODUCTION_SCHEMA_NAME = "public"
APPLICATION_LOGIN_ROLE = "cosmaneura_app"
APPLICATION_OWNER_ROLE = "cosmaneura_app_owner"
SECRET_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,19}$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
RESOURCE_GROUP_RE = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")
SUBSCRIPTION_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class CutoverError(RuntimeError):
    """A sanitized, operator-actionable cutover failure."""


@dataclass(frozen=True)
class Workload:
    label: str
    name: str
    resource_type: str
    cli_group: tuple[str, ...]
    revisioned: bool

    def resource_id(self, *, subscription_id: str, resource_group: str) -> str:
        return (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.App/{self.resource_type}/{self.name}"
        )


WORKLOADS = (
    Workload(
        label="worker",
        name="cosmaneura-prod-worker",
        resource_type="containerApps",
        cli_group=("containerapp",),
        revisioned=True,
    ),
    Workload(
        label="scheduler",
        name="cosmaneura-prod-scheduler",
        resource_type="containerApps",
        cli_group=("containerapp",),
        revisioned=True,
    ),
    Workload(
        label="api",
        name="cosmaneura-prod-app",
        resource_type="containerApps",
        cli_group=("containerapp",),
        revisioned=True,
    ),
    Workload(
        label="migration",
        name="cosmaneura-prod-migrate",
        resource_type="jobs",
        cli_group=("containerapp", "job"),
        revisioned=False,
    ),
)


def _run_az(arguments: Sequence[str]) -> str:
    command = ["az", *arguments, "--only-show-errors"]
    subprocess_environment = os.environ.copy()
    # An operator may have just run database_role.py in the same shell, where
    # DATABASE_URL is the privileged administrative credential.  Neither it
    # nor the versioned application credential belongs in an Azure CLI child.
    subprocess_environment.pop("DATABASE_URL", None)
    subprocess_environment.pop("DATABASE_URL_V2", None)
    subprocess_environment.pop("COSMANEURA_DATABASE_URL_V2", None)
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            env=subprocess_environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CutoverError("Azure CLI invocation failed") from error
    if result.returncode != 0:
        # Azure diagnostics can include resource configuration.  Do not echo
        # raw stdout/stderr from a credential rotation command.
        raise CutoverError("Azure CLI returned a failure")
    return result.stdout.strip()


class ArmClient:
    """Small ARM client that keeps secret values out of process arguments."""

    def __init__(
        self,
        token: str,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not token:
            raise CutoverError("Azure access token is empty")
        self._token = token
        self._opener = opener

    @classmethod
    def from_azure_cli(cls) -> ArmClient:
        token = _run_az(
            (
                "account",
                "get-access-token",
                "--resource",
                f"{ARM_ORIGIN}/",
                "--query",
                "accessToken",
                "--output",
                "tsv",
            )
        )
        return cls(token)

    def request(
        self,
        method: str,
        resource_id: str,
        *,
        action: str | None = None,
        body: dict[str, Any] | None = None,
        etag: str | None = None,
    ) -> dict[str, Any]:
        suffix = f"/{action}" if action else ""
        url = f"{ARM_ORIGIN}{resource_id}{suffix}?api-version={ARM_API_VERSION}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if etag:
            headers["If-Match"] = etag
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=120) as response:
                payload = response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            raise CutoverError("ARM request failed") from error
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as error:
            raise CutoverError("ARM returned an invalid response") from error
        if not isinstance(parsed, dict):
            raise CutoverError("ARM returned an unexpected response")
        return parsed

    def get_resource(self, resource_id: str) -> dict[str, Any]:
        return self.request("GET", resource_id)

    def list_secrets(self, resource_id: str) -> list[dict[str, Any]]:
        response = self.request("POST", resource_id, action="listSecrets")
        values = response.get("value")
        if not isinstance(values, list):
            raise CutoverError("ARM secret response is missing its value list")
        secrets: list[dict[str, Any]] = []
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("name"), str):
                raise CutoverError("ARM returned an invalid secret entry")
            secrets.append(value)
        return secrets

    def upsert_versioned_secret(
        self,
        resource_id: str,
        *,
        old_name: str,
        new_name: str,
        new_value: str,
    ) -> None:
        resource = self.get_resource(resource_id)
        etag = resource.get("etag")
        if not isinstance(etag, str) or not etag:
            raise CutoverError("resource ETag is missing")
        secrets = self.list_secrets(resource_id)
        by_name = {entry["name"]: entry for entry in secrets}
        if len(by_name) != len(secrets):
            raise CutoverError("resource has duplicate secret names")
        if old_name not in by_name:
            raise CutoverError("old rollback secret is missing")
        existing = by_name.get(new_name)
        if existing is not None:
            existing_value = existing.get("value")
            if not isinstance(existing_value, str) or not hmac.compare_digest(
                existing_value, new_value
            ):
                raise CutoverError(
                    "versioned secret already exists with a different value"
                )
            return
        secrets.append({"name": new_name, "value": new_value})
        self.request(
            "PATCH",
            resource_id,
            body={"properties": {"configuration": {"secrets": secrets}}},
            etag=etag,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            verified = {
                entry["name"]: entry for entry in self.list_secrets(resource_id)
            }
            if old_name in verified and new_name in verified:
                verified_value = verified[new_name].get("value")
                if isinstance(verified_value, str) and hmac.compare_digest(
                    verified_value, new_value
                ):
                    return
            time.sleep(2)
        raise CutoverError("secret upsert verification timed out")

    def reconcile_secret_values(
        self,
        resource_id: str,
        *,
        replacements: dict[str, str],
        required_names: set[str] | None = None,
    ) -> None:
        """Reconcile named value secrets without placing values on argv.

        Required and unrelated secrets are retained byte-for-byte.  Existing
        Key Vault-reference secrets are never silently converted to inline
        values, and an ETag protects actual configuration updates.  A
        required-name-only check performs no mutation and needs no ETag.
        """

        required = required_names or set()
        if not replacements and not required:
            raise CutoverError("database secret reconciliation is empty")
        if any(
            not SECRET_NAME_RE.fullmatch(name) or not value
            for name, value in replacements.items()
        ) or any(not SECRET_NAME_RE.fullmatch(name) for name in required):
            raise CutoverError("database secret reconciliation is invalid")
        if required.intersection(replacements):
            raise CutoverError("refusing to replace a required rollback secret")
        etag: str | None = None
        if replacements:
            resource = self.get_resource(resource_id)
            resource_etag = resource.get("etag")
            if not isinstance(resource_etag, str) or not resource_etag:
                raise CutoverError("resource ETag is missing")
            etag = resource_etag
        current = self.list_secrets(resource_id)
        by_name = {entry["name"]: entry for entry in current}
        if len(by_name) != len(current):
            raise CutoverError("resource has duplicate secret names")
        if not required.issubset(by_name):
            raise CutoverError("required rollback secret is missing")

        changed = False
        updated: list[dict[str, Any]] = []
        for entry in current:
            name = entry["name"]
            if name not in replacements:
                updated.append(entry)
                continue
            existing_value = entry.get("value")
            if not isinstance(existing_value, str):
                raise CutoverError("refusing to replace a non-value database secret")
            replacement = replacements[name]
            if hmac.compare_digest(existing_value, replacement):
                updated.append(entry)
            else:
                updated.append({"name": name, "value": replacement})
                changed = True
        for name, value in replacements.items():
            if name not in by_name:
                updated.append({"name": name, "value": value})
                changed = True
        if not changed:
            return
        if etag is None:
            raise CutoverError("resource ETag is missing")

        expected_names = {entry["name"] for entry in updated}
        self.request(
            "PATCH",
            resource_id,
            body={"properties": {"configuration": {"secrets": updated}}},
            etag=etag,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            verified_entries = self.list_secrets(resource_id)
            verified = {entry["name"]: entry for entry in verified_entries}
            if (
                len(verified) == len(verified_entries)
                and set(verified) == expected_names
            ):
                if all(
                    isinstance(verified[name].get("value"), str)
                    and hmac.compare_digest(verified[name]["value"], value)
                    for name, value in replacements.items()
                ):
                    return
            time.sleep(2)
        raise CutoverError("database secret reconciliation timed out")


def validate_secret_name(value: str) -> str:
    if not SECRET_NAME_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "secret names must be lowercase, at most 20 characters, and use "
            "only letters, digits, or hyphens"
        )
    return value


def validate_env_name(value: str) -> str:
    if not ENV_NAME_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("invalid environment variable name")
    return value


def validate_resource_group(value: str) -> str:
    if not RESOURCE_GROUP_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("invalid Azure resource group")
    return value


def validate_subscription(value: str) -> str:
    value = value.lower()
    if not SUBSCRIPTION_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("invalid Azure subscription ID")
    return value


def read_database_url(*, env_name: str, from_stdin: bool) -> str:
    env_value = os.environ.get(env_name)
    if env_value is not None and from_stdin:
        raise CutoverError(
            "provide the versioned URL through either the selected environment "
            "variable or stdin, not both"
        )
    if env_value is not None:
        value = env_value
    elif from_stdin:
        value = sys.stdin.readline().removesuffix("\n")
    else:
        raise CutoverError(
            f"versioned URL is missing; set {env_name} or use --database-url-stdin"
        )
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise CutoverError("versioned URL has an invalid format")
    return value


def read_migration_secret_bundle(stream: Any) -> dict[str, str]:
    payload = stream.read(65537)
    if len(payload) > 65536:
        raise CutoverError("migration database secret input is too large")
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise CutoverError("migration database secret input is invalid") from error
    if not isinstance(parsed, dict) or not set(parsed).issubset({"database-url-v2"}):
        raise CutoverError("migration database secret fields are invalid")
    result: dict[str, str] = {}
    for name, value in parsed.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not value
            or any(character in value for character in ("\x00", "\n", "\r"))
        ):
            raise CutoverError("migration database secret value is invalid")
        result[name] = value
    return result


def validate_migration_secret_bundle(
    values: dict[str, str], *, selected_secret: str
) -> None:
    if selected_secret not in {"database-url", "database-url-v2"}:
        raise CutoverError("selected migration database secret is invalid")
    if selected_secret == "database-url-v2" and selected_secret not in values:
        raise CutoverError("selected migration database secret is unavailable")
    versioned = values.get("database-url-v2")
    if versioned is not None:
        validate_database_url(
            versioned,
            expected_database=PRODUCTION_DATABASE_NAME,
            expected_login_role=APPLICATION_LOGIN_ROLE,
            expected_host=PRODUCTION_DATABASE_HOST,
        )


def validate_database_url(
    database_url: str,
    *,
    expected_database: str,
    expected_login_role: str,
    expected_host: str,
) -> None:
    if (
        expected_database != PRODUCTION_DATABASE_NAME
        or expected_login_role != APPLICATION_LOGIN_ROLE
        or expected_host.rstrip(".").lower() != PRODUCTION_DATABASE_HOST
    ):
        raise CutoverError("versioned URL validation target is unexpected")
    try:
        parsed = urllib.parse.urlsplit(database_url)
    except ValueError as error:
        raise CutoverError("versioned URL is invalid") from error
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise CutoverError("versioned URL must use PostgreSQL")
    if (
        parsed.username != expected_login_role
        or not parsed.password
        or parsed.netloc.count("@") != 1
    ):
        raise CutoverError("versioned URL uses an unexpected login")
    if re.search(r"%(?![0-9A-Fa-f]{2})", parsed.password):
        raise CutoverError("versioned URL password encoding is invalid")
    decoded_password = urllib.parse.unquote(parsed.password)
    if len(decoded_password) < 32 or len(decoded_password) > 256:
        raise CutoverError("versioned URL password length is invalid")
    if any(
        ord(character) < 33 or ord(character) > 126 for character in decoded_password
    ):
        raise CutoverError("versioned URL password format is invalid")
    try:
        canonical_host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError as error:
        raise CutoverError("versioned URL is invalid") from error
    if canonical_host != expected_host.rstrip(".").lower():
        raise CutoverError("versioned URL uses an unexpected host")
    if port not in {None, 5432}:
        raise CutoverError("versioned URL uses an unexpected port")
    if parsed.path != f"/{expected_database}":
        raise CutoverError("versioned URL uses an unexpected database")
    if parsed.query != "sslmode=require":
        raise CutoverError("versioned URL options must be exactly sslmode=require")
    if parsed.fragment:
        raise CutoverError("versioned URL must not contain a fragment")


def verify_database_login(
    database_url: str,
    *,
    expected_database: str,
    expected_login_role: str,
    expected_owner_role: str,
    schema: str,
) -> None:
    if (
        expected_database != PRODUCTION_DATABASE_NAME
        or expected_login_role != APPLICATION_LOGIN_ROLE
        or expected_owner_role != APPLICATION_OWNER_ROLE
        or schema != PRODUCTION_SCHEMA_NAME
    ):
        raise CutoverError("application database verification target is unexpected")
    try:
        import psycopg
    except ImportError as error:
        raise CutoverError("PostgreSQL client dependency is unavailable") from error
    try:
        with psycopg.connect(database_url, connect_timeout=15) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT session_user, current_user, current_database()")
                identity = cursor.fetchone()
                if identity != (
                    expected_login_role,
                    expected_owner_role,
                    expected_database,
                ):
                    raise CutoverError("application database identity is unexpected")
                cursor.execute(
                    """
                    SELECT rolname, rolsuper, rolcreatedb, rolcreaterole,
                           rolcanlogin, rolreplication, rolbypassrls, rolinherit
                      FROM pg_roles
                     WHERE rolname = ANY(%s)
                    """,
                    ([expected_login_role, expected_owner_role],),
                )
                roles = {row[0]: row[1:] for row in cursor.fetchall()}
                if roles.get(expected_login_role) != (
                    False,
                    False,
                    False,
                    True,
                    False,
                    False,
                    True,
                ):
                    raise CutoverError(
                        "application login is not configured least-privilege"
                    )
                if roles.get(expected_owner_role) != (
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                ):
                    raise CutoverError(
                        "application owner is not configured least-privilege"
                    )
                cursor.execute(
                    """
                    SELECT membership.admin_option,
                           membership.inherit_option,
                           membership.set_option,
                           grantor.rolname
                      FROM pg_auth_members membership
                      JOIN pg_roles granted_role
                        ON granted_role.oid = membership.roleid
                      JOIN pg_roles member_role
                        ON member_role.oid = membership.member
                      JOIN pg_roles grantor ON grantor.oid = membership.grantor
                     WHERE granted_role.rolname = %s
                       AND member_role.rolname = %s
                    """,
                    (expected_owner_role, expected_login_role),
                )
                membership = cursor.fetchone()
                if (
                    membership is None
                    or membership[:3] != (False, True, True)
                    or membership[3] in {expected_login_role, expected_owner_role}
                ):
                    raise CutoverError("application role membership is unexpected")
                cursor.execute(
                    """
                    SELECT schema_owner.rolname,
                           database_owner.rolname,
                           to_regclass(%s) IS NOT NULL
                      FROM pg_namespace namespace
                      JOIN pg_roles schema_owner
                        ON schema_owner.oid = namespace.nspowner
                      JOIN pg_database database
                        ON database.datname = current_database()
                      JOIN pg_roles database_owner
                        ON database_owner.oid = database.datdba
                     WHERE namespace.nspname = %s
                    """,
                    (f"{schema}.alembic_version", schema),
                )
                ownership = cursor.fetchone()
                if ownership is None or ownership[0] != expected_owner_role:
                    raise CutoverError("application schema owner is unexpected")
                if ownership[1] in {expected_owner_role, expected_login_role}:
                    raise CutoverError("database is owned by an application role")
                if ownership[2] is not True:
                    raise CutoverError("Alembic version table is unavailable")
    except CutoverError:
        raise
    except psycopg.Error as error:
        raise CutoverError("application database verification failed") from error


def azure_account_subscription() -> str:
    return _run_az(("account", "show", "--query", "id", "--output", "tsv"))


def show_workload(
    workload: Workload, *, subscription_id: str, resource_group: str
) -> dict[str, Any]:
    raw = _run_az(
        (
            *workload.cli_group,
            "show",
            "--name",
            workload.name,
            "--resource-group",
            resource_group,
            "--subscription",
            subscription_id,
            "--output",
            "json",
        )
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CutoverError("Azure CLI returned invalid workload JSON") from error
    if not isinstance(value, dict):
        raise CutoverError("Azure CLI returned unexpected workload JSON")
    return value


def database_secret_reference(resource: dict[str, Any]) -> tuple[str, str]:
    try:
        containers = resource["properties"]["template"]["containers"]
    except (KeyError, TypeError) as error:
        raise CutoverError("workload has no container template") from error
    if not isinstance(containers, list) or len(containers) != 1:
        raise CutoverError("workload must have exactly one container")
    container = containers[0]
    env_values = container.get("env", [])
    matches = [entry for entry in env_values if entry.get("name") == "DATABASE_URL"]
    if len(matches) != 1:
        raise CutoverError("workload must have one DATABASE_URL setting")
    secret_ref = matches[0].get("secretRef")
    container_name = container.get("name")
    if not isinstance(secret_ref, str) or not isinstance(container_name, str):
        raise CutoverError("DATABASE_URL must reference a named secret")
    return secret_ref, container_name


def configured_secret_names(resource: dict[str, Any]) -> set[str]:
    try:
        secrets = resource["properties"]["configuration"]["secrets"]
    except (KeyError, TypeError) as error:
        raise CutoverError("workload secret configuration is missing") from error
    if not isinstance(secrets, list):
        raise CutoverError("workload secret configuration is invalid")
    names: set[str] = set()
    for secret in secrets:
        if not isinstance(secret, dict) or not isinstance(secret.get("name"), str):
            raise CutoverError("workload has an invalid secret entry")
        names.add(secret["name"])
    return names


def update_database_reference(
    workload: Workload,
    *,
    subscription_id: str,
    resource_group: str,
    secret_name: str,
    revision_suffix: str,
) -> str | None:
    current = show_workload(
        workload,
        subscription_id=subscription_id,
        resource_group=resource_group,
    )
    current_secret, container_name = database_secret_reference(current)
    if current_secret == secret_name:
        if secret_name not in configured_secret_names(current):
            raise CutoverError("selected database secret is missing")
        if not workload.revisioned:
            return None
        latest_revision = current.get("properties", {}).get("latestRevisionName")
        if not isinstance(latest_revision, str) or not latest_revision:
            raise CutoverError("workload latest revision is missing")
        return latest_revision
    arguments = [
        *workload.cli_group,
        "update",
        "--name",
        workload.name,
        "--resource-group",
        resource_group,
        "--subscription",
        subscription_id,
        "--container-name",
        container_name,
        "--set-env-vars",
        f"DATABASE_URL=secretref:{secret_name}",
        "--output",
        "none",
    ]
    if workload.revisioned:
        arguments.extend(("--revision-suffix", revision_suffix))
    _run_az(arguments)
    if workload.revisioned:
        return f"{workload.name}--{revision_suffix}"
    return None


def verify_revision(
    workload: Workload,
    *,
    subscription_id: str,
    resource_group: str,
    expected_secret: str,
    expected_revision_name: str | None,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        resource = show_workload(
            workload,
            subscription_id=subscription_id,
            resource_group=resource_group,
        )
        current_ref, _ = database_secret_reference(resource)
        if current_ref != expected_secret:
            time.sleep(5)
            continue
        if not workload.revisioned:
            return
        revision_name = resource.get("properties", {}).get("latestRevisionName")
        if revision_name != expected_revision_name:
            time.sleep(5)
            continue
        raw = _run_az(
            (
                "containerapp",
                "revision",
                "show",
                "--name",
                workload.name,
                "--resource-group",
                resource_group,
                "--subscription",
                subscription_id,
                "--revision",
                revision_name,
                "--output",
                "json",
            )
        )
        try:
            revision = json.loads(raw)
            properties = revision["properties"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise CutoverError("Azure CLI returned invalid revision JSON") from error
        provisioning = properties.get("provisioningState")
        running = properties.get("runningState")
        health = properties.get("healthState")
        if provisioning == "Failed" or running == "Failed":
            raise CutoverError("workload revision failed")
        if (
            provisioning == "Provisioned"
            and health == "Healthy"
            and running in {"Running", "RunningAtMaxScale"}
        ):
            return
        time.sleep(5)
    raise CutoverError("workload verification timed out")


class CutoverOperator:
    def __init__(
        self,
        *,
        arm: ArmClient,
        subscription_id: str,
        resource_group: str,
        old_secret_name: str,
        new_secret_name: str,
        timeout_seconds: int,
    ) -> None:
        self.arm = arm
        self.subscription_id = subscription_id
        self.resource_group = resource_group
        self.old_secret_name = old_secret_name
        self.new_secret_name = new_secret_name
        self.timeout_seconds = timeout_seconds
        self.revision_run = datetime.now(UTC).strftime("%y%m%d%H%M%S%f")

    def resource_id(self, workload: Workload) -> str:
        return workload.resource_id(
            subscription_id=self.subscription_id,
            resource_group=self.resource_group,
        )

    def status(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for workload in WORKLOADS:
            resource = show_workload(
                workload,
                subscription_id=self.subscription_id,
                resource_group=self.resource_group,
            )
            secret_ref, _ = database_secret_reference(resource)
            names = configured_secret_names(resource)
            if self.old_secret_name not in names:
                state = "missing-rollback-secret"
            elif secret_ref == self.old_secret_name:
                state = "old"
            elif secret_ref == self.new_secret_name:
                state = "new"
            else:
                state = "unexpected"
            result.append({"state": state, "workload": workload.label})
        return result

    def preflight_old_state(self) -> None:
        states = self.status()
        if any(item["state"] != "old" for item in states):
            raise CutoverError(
                "all workloads must reference the old rollback secret before apply"
            )

    def install_versioned_secret(self, database_url: str) -> None:
        for workload in WORKLOADS:
            self.arm.upsert_versioned_secret(
                self.resource_id(workload),
                old_name=self.old_secret_name,
                new_name=self.new_secret_name,
                new_value=database_url,
            )

    def reconcile_migration_job(
        self, values: dict[str, str], *, selected_secret: str
    ) -> None:
        migration = WORKLOADS[-1]
        if migration.label != "migration" or migration.revisioned:
            raise CutoverError("migration workload definition is invalid")
        if self.old_secret_name in values:
            raise CutoverError("refusing to replace the migration rollback secret")
        self.arm.reconcile_secret_values(
            self.resource_id(migration),
            replacements=values,
            required_names={self.old_secret_name},
        )
        expected_revision = update_database_reference(
            migration,
            subscription_id=self.subscription_id,
            resource_group=self.resource_group,
            secret_name=selected_secret,
            revision_suffix="unused",
        )
        verify_revision(
            migration,
            subscription_id=self.subscription_id,
            resource_group=self.resource_group,
            expected_secret=selected_secret,
            expected_revision_name=expected_revision,
            timeout_seconds=self.timeout_seconds,
        )

    def _switch(self, workload: Workload, secret_name: str) -> None:
        if secret_name == self.new_secret_name:
            direction = "n"
        elif secret_name == self.old_secret_name:
            direction = "o"
        else:
            raise CutoverError("refusing to select an unknown database secret")
        label_code = {
            "api": "ap",
            "scheduler": "sc",
            "worker": "wo",
        }.get(workload.label, "mg")
        suffix = f"d2-{self.revision_run}-{label_code}-{direction}"
        expected_revision_name = update_database_reference(
            workload,
            subscription_id=self.subscription_id,
            resource_group=self.resource_group,
            secret_name=secret_name,
            revision_suffix=suffix,
        )
        verify_revision(
            workload,
            subscription_id=self.subscription_id,
            resource_group=self.resource_group,
            expected_secret=secret_name,
            expected_revision_name=expected_revision_name,
            timeout_seconds=self.timeout_seconds,
        )

    def apply(self, database_url: str) -> None:
        self.preflight_old_state()
        self.install_versioned_secret(database_url)
        switched: list[Workload] = []
        try:
            for workload in WORKLOADS:
                switched.append(workload)
                self._switch(workload, self.new_secret_name)
        except CutoverError as error:
            rollback_failed = False
            for workload in reversed(switched):
                try:
                    self._switch(workload, self.old_secret_name)
                except CutoverError:
                    rollback_failed = True
            if rollback_failed:
                raise CutoverError(
                    "cutover failed and automatic rollback was incomplete"
                ) from error
            raise CutoverError("cutover failed and was rolled back") from error

    def rollback(self) -> None:
        states = self.status()
        by_label = {item["workload"]: item["state"] for item in states}
        if any(state not in {"old", "new"} for state in by_label.values()):
            raise CutoverError("rollback state is incomplete or unexpected")
        rollback_failed = False
        for workload in reversed(WORKLOADS):
            if by_label[workload.label] == "new":
                try:
                    self._switch(workload, self.old_secret_name)
                except CutoverError:
                    rollback_failed = True
        if rollback_failed:
            raise CutoverError("rollback was incomplete")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    mode.add_argument(
        "--reconcile-migration-secrets-stdin",
        action="store_true",
        help="securely reconcile migration database secrets from a JSON stdin map",
    )
    parser.add_argument(
        "--subscription-id",
        type=validate_subscription,
        default=os.environ.get("AZURE_SUBSCRIPTION_ID"),
    )
    parser.add_argument(
        "--resource-group",
        type=validate_resource_group,
        default="cosmaneura-prod",
    )
    parser.add_argument("--confirm-subscription", type=validate_subscription)
    parser.add_argument("--confirm-resource-group", type=validate_resource_group)
    parser.add_argument(
        "--old-secret-name", default="database-url", type=validate_secret_name
    )
    parser.add_argument(
        "--new-secret-name", default="database-url-v2", type=validate_secret_name
    )
    parser.add_argument(
        "--selected-secret-name", default="database-url", type=validate_secret_name
    )
    parser.add_argument(
        "--database-url-env",
        default="COSMANEURA_DATABASE_URL_V2",
        type=validate_env_name,
    )
    parser.add_argument("--database-url-stdin", action="store_true")
    parser.add_argument(
        "--acknowledge-startup-alembic",
        action="store_true",
        help="confirm that every process imports and runs Alembic at startup",
    )
    parser.add_argument(
        "--acknowledge-no-infra-apply-window",
        action="store_true",
        help="confirm that Terraform applies are held during the cutover window",
    )
    parser.add_argument(
        "--expected-database",
        default=PRODUCTION_DATABASE_NAME,
        choices=(PRODUCTION_DATABASE_NAME,),
    )
    parser.add_argument(
        "--expected-login-role",
        default=APPLICATION_LOGIN_ROLE,
        choices=(APPLICATION_LOGIN_ROLE,),
    )
    parser.add_argument(
        "--expected-owner-role",
        default=APPLICATION_OWNER_ROLE,
        choices=(APPLICATION_OWNER_ROLE,),
    )
    parser.add_argument(
        "--schema",
        default=PRODUCTION_SCHEMA_NAME,
        choices=(PRODUCTION_SCHEMA_NAME,),
    )
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.subscription_id:
        print(json.dumps({"code": "subscription_missing", "result": "failed"}))
        return 2
    if args.old_secret_name == args.new_secret_name:
        print(json.dumps({"code": "secret_names_not_distinct", "result": "failed"}))
        return 2
    if args.timeout_seconds < 30 or args.timeout_seconds > 900:
        print(json.dumps({"code": "timeout_invalid", "result": "failed"}))
        return 2

    mutating = args.apply or args.rollback or args.reconcile_migration_secrets_stdin
    if mutating and (
        args.confirm_subscription != args.subscription_id
        or args.confirm_resource_group != args.resource_group
    ):
        print(json.dumps({"code": "confirmation_mismatch", "result": "failed"}))
        return 2
    if args.apply and (
        not args.acknowledge_startup_alembic
        or not args.acknowledge_no_infra_apply_window
    ):
        print(
            json.dumps(
                {"code": "operational_acknowledgement_missing", "result": "failed"}
            )
        )
        return 2

    database_url: str | None = None
    migration_values: dict[str, str] | None = None
    try:
        if args.apply:
            try:
                database_url = read_database_url(
                    env_name=args.database_url_env,
                    from_stdin=args.database_url_stdin,
                )
            finally:
                # Never let the selected secret environment variable reach an az
                # subprocess, including when a custom variable name was selected.
                os.environ.pop(args.database_url_env, None)
            validate_database_url(
                database_url,
                expected_database=args.expected_database,
                expected_login_role=args.expected_login_role,
                expected_host=PRODUCTION_DATABASE_HOST,
            )
            verify_database_login(
                database_url,
                expected_database=args.expected_database,
                expected_login_role=args.expected_login_role,
                expected_owner_role=args.expected_owner_role,
                schema=args.schema,
            )
        if args.reconcile_migration_secrets_stdin:
            migration_values = read_migration_secret_bundle(sys.stdin)
            validate_migration_secret_bundle(
                migration_values,
                selected_secret=args.selected_secret_name,
            )
    except CutoverError:
        print(json.dumps({"code": "cutover_failed", "result": "failed"}))
        return 1

    try:
        active_subscription = azure_account_subscription().lower()
        if active_subscription != args.subscription_id:
            raise CutoverError("active Azure subscription does not match")
        arm = ArmClient.from_azure_cli()
        operator = CutoverOperator(
            arm=arm,
            subscription_id=args.subscription_id,
            resource_group=args.resource_group,
            old_secret_name=args.old_secret_name,
            new_secret_name=args.new_secret_name,
            timeout_seconds=args.timeout_seconds,
        )
        if args.reconcile_migration_secrets_stdin:
            if migration_values is None:
                raise CutoverError("migration database secrets were not loaded")
            operator.reconcile_migration_job(
                migration_values,
                selected_secret=args.selected_secret_name,
            )
            print(
                json.dumps(
                    {
                        "mode": "reconcile-migration-secrets",
                        "result": "succeeded",
                        "selected_secret": args.selected_secret_name,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.apply:
            if database_url is None:
                raise CutoverError("versioned URL was not loaded")
            operator.apply(database_url)
            print(
                json.dumps(
                    {
                        "mode": "apply",
                        "result": "succeeded",
                        "workloads": [item.label for item in WORKLOADS],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.rollback:
            operator.rollback()
            print(
                json.dumps(
                    {
                        "mode": "rollback",
                        "result": "succeeded",
                        "versioned_secret_retained": True,
                    },
                    sort_keys=True,
                )
            )
            return 0

        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "result": "planned",
                    "sequence": operator.status(),
                },
                sort_keys=True,
            )
        )
        return 0
    except CutoverError:
        print(json.dumps({"code": "cutover_failed", "result": "failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
