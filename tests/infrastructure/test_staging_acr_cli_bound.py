from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path
from typing import TextIO
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "staging_acr_cli_bound", ROOT / "scripts" / "staging_acr_cli_bound.py"
)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

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


def bound(command: list[str]) -> list[str]:
    return [*command, "--subscription", m.SUBSCRIPTION]


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
        with pytest.raises(m.BoundAcrRefused):
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
    access_token = record["accessToken"]
    assert isinstance(access_token, str)
    pieces = access_token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(pieces[1] + "=="))
    payload[claim] = value
    record["accessToken"] = f"{pieces[0]}.{_segment(payload)}.{pieces[2]}"
    with patch.object(m, "sha", side_effect=_identity_sha):
        with pytest.raises(m.BoundAcrRefused):
            m.validate_token_record(record, m.MANAGEMENT_AUDIENCES, now=NOW)


def test_exact_publisher_commands_are_accepted() -> None:
    digest = "sha256:" + "a" * 64
    commands = (
        [
            "acr",
            "login",
            "--name",
            m.REGISTRY,
            "--expose-token",
            "--only-show-errors",
            "--output",
            "json",
        ],
        [
            "acr",
            "repository",
            "show-tags",
            "--name",
            m.REGISTRY,
            "--repository",
            m.REPOSITORY,
            "--query",
            f"[?@=='{m.CANDIDATE}']",
            "--output",
            "json",
        ],
        [
            "acr",
            "import",
            "--name",
            m.REGISTRY,
            "--registry",
            m.REGISTRY,
            "--source",
            f"{m.REPOSITORY}@{digest}",
            "--image",
            f"{m.REPOSITORY}:{m.CANDIDATE}",
            "--only-show-errors",
            "--output",
            "none",
        ],
        [
            "acr",
            "manifest",
            "show-metadata",
            "--registry",
            m.REGISTRY,
            "--name",
            f"{m.REPOSITORY}:{m.CANDIDATE}",
            "--query",
            "digest",
            "--output",
            "tsv",
        ],
        [
            "acr",
            "repository",
            "update",
            "--name",
            m.REGISTRY,
            "--image",
            f"{m.REPOSITORY}:{m.CANDIDATE}",
            "--write-enabled",
            "false",
            "--delete-enabled",
            "false",
            "--only-show-errors",
            "--output",
            "none",
        ],
        [
            "acr",
            "repository",
            "show",
            "--name",
            m.REGISTRY,
            "--image",
            f"{m.REPOSITORY}:{m.CANDIDATE}",
            "--output",
            "json",
        ],
    )
    for command in commands:
        m.validate_argv(bound(command))


@pytest.mark.parametrize(
    "command",
    (
        ["--verify-only"],
        ["containerapp", "show", "--name", "cosmaneura-staging-app"],
        ["acr", "credential", "show", "--name", m.REGISTRY],
        ["acr", "login", "--name", m.REGISTRY],
        ["acr", "login", f"--name={m.REGISTRY}", "--expose-token"],
        [
            "acr",
            "import",
            "--name",
            m.REGISTRY,
            "--registry",
            m.REGISTRY,
            "--source",
            f"{m.REPOSITORY}@sha256:{'a' * 64}",
            "--image",
            f"{m.REPOSITORY}:{m.CANDIDATE}",
            "--force",
        ],
        [
            "acr",
            "repository",
            "update",
            "--name",
            m.REGISTRY,
            "--image",
            f"{m.REPOSITORY}:latest",
            "--write-enabled",
            "false",
            "--delete-enabled",
            "false",
        ],
        [
            "acr",
            "repository",
            "show-tags",
            "--name",
            "cosmaneuraprodregistry",
            "--repository",
            m.REPOSITORY,
        ],
    ),
)
def test_every_nonexact_or_nonpublication_command_is_rejected(
    command: list[str],
) -> None:
    with pytest.raises(m.BoundAcrRefused):
        m.validate_argv(bound(command))


def manifest_command(*, read: bool, digest: str) -> list[str]:
    if read:
        return bound(
            [
                "acr",
                "manifest",
                "show-metadata",
                "--registry",
                m.REGISTRY,
                "--name",
                f"{m.REPOSITORY}@{digest}",
                "--output",
                "json",
            ]
        )
    return bound(
        [
            "acr",
            "repository",
            "update",
            "--name",
            m.REGISTRY,
            "--image",
            f"{m.REPOSITORY}@{digest}",
            "--write-enabled",
            "false",
            "--delete-enabled",
            "false",
            "--only-show-errors",
            "--output",
            "none",
        ]
    )


class CandidateTagCli:
    def __init__(self, tag: dict[str, object], *, status: int = 0):
        self.tag, self.status = tag, status
        self.calls: list[list[str]] = []

    def invoke(self, argv: list[str], *, out_file: TextIO | None = None) -> int:
        self.calls.append(argv)
        if out_file is not None:
            out_file.write(json.dumps(self.tag))
            return self.status
        return 0


def protected_tag(digest: str) -> dict[str, object]:
    return {
        "name": m.CANDIDATE,
        "digest": digest,
        "changeableAttributes": {
            "writeEnabled": False,
            "deleteEnabled": False,
            "readEnabled": True,
            "listEnabled": True,
        },
    }


@pytest.mark.parametrize("read", (False, True))
def test_manifest_lock_and_read_use_only_the_protected_candidate_digest(read):
    digest = "sha256:" + "a" * 64
    command = manifest_command(read=read, digest=digest)
    cli = CandidateTagCli(protected_tag(digest))
    assert m.validate_argv(command) == digest
    assert m.invoke_bound_command(cli, command, m.CANDIDATE) == 0
    assert cli.calls[0] == bound(
        [
            "acr",
            "repository",
            "show",
            "--name",
            m.REGISTRY,
            "--image",
            f"{m.REPOSITORY}:{m.CANDIDATE}",
            "--output",
            "json",
        ]
    )
    assert cli.calls[1:] == [command]


@pytest.mark.parametrize("read", (False, True))
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("digest", "sha256:" + "b" * 64),
        ("name", "other-candidate"),
        ("writeEnabled", True),
        ("deleteEnabled", True),
        ("readEnabled", False),
        ("listEnabled", False),
        ("deleteEnabled", "false"),
    ),
)
def test_manifest_operation_is_not_invoked_for_an_unbound_or_unprotected_tag(
    read, field, value
):
    digest = "sha256:" + "a" * 64
    tag = protected_tag(digest)
    if field in {"name", "digest"}:
        tag[field] = value
    else:
        attributes = tag["changeableAttributes"]
        assert isinstance(attributes, dict)
        attributes[field] = value
    cli = CandidateTagCli(tag)
    with pytest.raises(m.BoundAcrRefused):
        m.invoke_bound_command(
            cli, manifest_command(read=read, digest=digest), m.CANDIDATE
        )
    assert len(cli.calls) == 1


def test_failed_candidate_readback_prevents_manifest_mutation():
    digest = "sha256:" + "a" * 64
    cli = CandidateTagCli(protected_tag(digest), status=1)
    with pytest.raises(m.BoundAcrRefused):
        m.invoke_bound_command(
            cli, manifest_command(read=False, digest=digest), m.CANDIDATE
        )
    assert len(cli.calls) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("--name", "cosmaneuraprodregistry"),
        ("--write-enabled", "true"),
        ("--delete-enabled", "true"),
        ("--image", "other/repository@sha256:" + "a" * 64),
    ),
)
def test_manifest_admission_rejects_broader_registry_or_permission_changes(
    field, value
):
    command = manifest_command(read=False, digest="sha256:" + "a" * 64)
    command[command.index(field) + 1] = value
    cli = CandidateTagCli(protected_tag("sha256:" + "a" * 64))
    with pytest.raises(m.BoundAcrRefused):
        m.invoke_bound_command(cli, command, m.CANDIDATE)
    assert cli.calls == []
