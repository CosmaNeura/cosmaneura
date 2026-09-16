from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

MODULE_NAME = "staging_azure_cli_bound"
MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "staging_azure_cli_bound.py"
)
SPEC = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = MODULE
SPEC.loader.exec_module(MODULE)
m = MODULE

NOW = 2_000_000_000
FIXTURE_USER = "fixture-user"
FIXTURE_OBJECT = "fixture-object"


def _segment(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _record(audience: str = "https://management.azure.com/") -> dict[str, object]:
    expiry = NOW + 3600
    claims = {
        "aud": audience,
        "email": FIXTURE_USER,
        "exp": expiry,
        "idtyp": "user",
        "nbf": NOW - 30,
        "oid": FIXTURE_OBJECT,
        "tid": m.TENANT,
    }
    token = f"{_segment({'alg': 'none'})}.{_segment(claims)}.{('x' * 64)}"
    return {
        "accessToken": token,
        "expires_on": expiry - 2,
        "subscription": m.SUBSCRIPTION,
        "tenant": m.TENANT,
        "tokenType": "Bearer",
    }


def _identity_sha(value: str) -> str:
    if value == FIXTURE_USER:
        return m.USERNAME_SHA256
    if value == FIXTURE_OBJECT:
        return m.OBJECT_ID_SHA256
    return "0" * 64


def test_exact_user_token_record_is_accepted_for_each_required_audience() -> None:
    with patch.object(m, "sha", side_effect=_identity_sha):
        management = _record()
        assert m.validate_token_record(management, m.MANAGEMENT_AUDIENCES, now=NOW) == (
            management["accessToken"],
            NOW + 3600,
        )

        registry = _record(m.REGISTRY_AUDIENCE)
        registry["expires_on"] = str(NOW + 3600)
        assert m.validate_token_record(registry, {m.REGISTRY_AUDIENCE}, now=NOW) == (
            registry["accessToken"],
            NOW + 3600,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("subscription", "00000000-0000-0000-0000-000000000000"),
        ("tenant", "00000000-0000-0000-0000-000000000000"),
        ("tokenType", "Basic"),
        ("expires_on", NOW + 3500),
    ),
)
def test_token_record_rejects_wrong_outer_binding(field: str, value: object) -> None:
    record = _record()
    record[field] = value
    with patch.object(m, "sha", side_effect=_identity_sha):
        with pytest.raises(m.BoundAzureRefused):
            m.validate_token_record(record, m.MANAGEMENT_AUDIENCES, now=NOW)


@pytest.mark.parametrize(
    ("claim", "value"),
    (
        ("aud", "https://graph.microsoft.com/"),
        ("email", "other-user"),
        ("exp", NOW + 299),
        ("idtyp", "app"),
        ("nbf", NOW + 31),
        ("oid", "other-object"),
        ("tid", "00000000-0000-0000-0000-000000000000"),
    ),
)
def test_token_record_rejects_wrong_signed_claim(claim: str, value: object) -> None:
    record = _record()
    payload = json.loads(
        base64.urlsafe_b64decode(
            record["accessToken"].split(".")[1] + "=="  # type: ignore[union-attr]
        )
    )
    payload[claim] = value
    pieces = record["accessToken"].split(".")  # type: ignore[union-attr]
    record["accessToken"] = f"{pieces[0]}.{_segment(payload)}.{pieces[2]}"
    with patch.object(m, "sha", side_effect=_identity_sha):
        with pytest.raises(m.BoundAzureRefused):
            m.validate_token_record(record, m.MANAGEMENT_AUDIENCES, now=NOW)


def test_exact_staging_acr_commands_are_accepted() -> None:
    digest = "sha256:" + "a" * 64
    subscription = ["--subscription", m.SUBSCRIPTION]
    commands = (
        ["acr", "credential", "show", "-n", m.STAGING_REGISTRY],
        [
            "acr",
            "manifest",
            "show-metadata",
            "--registry",
            m.STAGING_REGISTRY,
            "--name",
            f"{m.STAGING_REPOSITORY}:{m.CANDIDATE_SHA}",
        ],
        [
            "acr",
            "manifest",
            "show-metadata",
            "--registry",
            m.STAGING_REGISTRY,
            "--name",
            f"{m.STAGING_REPOSITORY}@{digest}",
        ],
    )
    for command in commands:
        m.validate_argv(command + subscription)


def test_exact_staging_containerapp_commands_are_accepted() -> None:
    image = f"{m.STAGING_REGISTRY_HOST}/{m.STAGING_REPOSITORY}@sha256:{'a' * 64}"
    subscription = ["--subscription", m.SUBSCRIPTION]
    for prefix in m.ALLOWED_CONTAINERAPP_COMMANDS:
        command = [
            *prefix,
            "--name",
            "cosmaneura-staging-app"
            if prefix[1] != "job"
            else "cosmaneura-staging-migrate",
            "--resource-group",
            m.STAGING_RESOURCE_GROUP,
        ]
        if prefix in {
            ("containerapp", "update"),
            ("containerapp", "job", "create"),
            ("containerapp", "job", "start"),
        }:
            command.extend(["--image", image])
        m.validate_argv(command + subscription)


@pytest.mark.parametrize(
    "command",
    (
        ["containerapp", "list", "-g", "cosmaneura-staging"],
        [
            "containerapp",
            "show",
            "-n",
            "cosmaneura-prod-app",
            "-g",
            "cosmaneura-staging",
        ],
        ["containerapp", "show", "-n", "cosmaneura-staging-app", "-g", "production"],
        ["acr", "login", "--name", "cosmaneuraprodregistry", "--expose-token"],
        ["acr", "login", "--name", "cosmaneurastagingregistry"],
        ["acr", "import", "--name", "cosmaneurastagingregistry"],
        ["acr", "repository", "update", "--name", "cosmaneurastagingregistry"],
        [
            "acr",
            "manifest",
            "show-metadata",
            "--registry",
            "cosmaneurastagingregistry",
            "--name",
            f"{m.STAGING_REPOSITORY}:{'b' * 40}",
        ],
    ),
)
def test_non_staging_or_unapproved_commands_are_rejected(command: list[str]) -> None:
    with pytest.raises(m.BoundAzureRefused):
        m.validate_argv([*command, "--subscription", m.SUBSCRIPTION])


@pytest.mark.parametrize(
    "command",
    (
        [
            "containerapp",
            "update",
            "-n",
            "cosmaneura-staging-app",
            "-g",
            m.STAGING_RESOURCE_GROUP,
            "--image=other.invalid/image:latest",
        ],
        [
            "containerapp",
            "show",
            "-ncosmaneura-prod-app",
            "-n",
            "cosmaneura-staging-app",
            "-g",
            m.STAGING_RESOURCE_GROUP,
        ],
        [
            "containerapp",
            "show",
            "-n",
            "cosmaneura-staging-app",
            "-gother",
            "-g",
            m.STAGING_RESOURCE_GROUP,
        ],
        [
            "acr",
            "manifest",
            "show-metadata",
            "--registry",
            m.STAGING_REGISTRY,
            "--name",
            f"{m.STAGING_REPOSITORY}:{m.CANDIDATE_SHA}",
            "-nother",
        ],
        [
            "acr",
            "credential",
            "show",
            "-n",
            m.STAGING_REGISTRY,
            "-nother",
        ],
        [
            "containerapp",
            "show",
            "-n",
            "cosmaneura-staging-app",
            "-g",
            m.STAGING_RESOURCE_GROUP,
            "--resource-g",
            "other",
        ],
        [
            "containerapp",
            "update",
            "-n",
            "cosmaneura-staging-app",
            "-g",
            m.STAGING_RESOURCE_GROUP,
            "--image",
            "cosmaneurastagingregistryxazurecr.io/"
            f"{m.STAGING_REPOSITORY}@sha256:{'a' * 64}",
        ],
    ),
)
def test_ambiguous_or_attached_option_spellings_are_rejected(
    command: list[str],
) -> None:
    with pytest.raises(m.BoundAzureRefused):
        m.validate_argv([*command, "--subscription", m.SUBSCRIPTION])


def test_scope_normalization_never_splits_a_string_into_characters() -> None:
    scope = "https://management.azure.com/.default"
    assert m.normalized_scopes(scope) == {scope}
    assert m.normalized_scopes((scope,)) == {scope}
