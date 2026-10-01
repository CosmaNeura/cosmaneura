"""Exact SDK absence evidence, with synthetic responses and no live requests."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from clerk_backend_api.models import ClerkError, ClerkErrors, ClerkErrorsData, SDKError

from app.configuration import authority
from app.configuration import clerk as configuration
from app.configuration.authority import AuthorityKernelSettings
from app.services.practice_owner_identity_contract import OwnerIdentityAbsence

SUBJECT = "user_SyntheticOrphanedOwner"
ISSUER = "https://synthetic-owner.clerk.invalid"


def missing_error(status=404, codes=("resource_not_found",)):
    return ClerkErrors(
        raw_response=httpx.Response(status, json={"errors": []}),
        data=ClerkErrorsData(
            errors=[
                ClerkError(code=code, message="synthetic", long_message="synthetic")
                for code in codes
            ]
        ),
    )


@pytest.fixture
def probe(monkeypatch):
    settings = AuthorityKernelSettings(
        enabled=False,
        token_ttl_seconds=900,
        token_refresh_after_seconds=300,
        clerk_issuer=ISSUER,
    )
    monkeypatch.setattr(authority, "AUTHORITY_KERNEL_SETTINGS", settings)
    monkeypatch.setattr(configuration, "python_environment", lambda: "staging")
    get = Mock(side_effect=missing_error())
    monkeypatch.setattr(
        configuration, "clerk", SimpleNamespace(users=SimpleNamespace(get=get))
    )
    return get, settings


def test_exact_typed_404_is_bound_to_current_runtime_and_issuer(probe):
    get, _ = probe
    result = configuration.prove_absent_owner_identity(SUBJECT)
    assert result == OwnerIdentityAbsence(
        subject=SUBJECT,
        environment="staging",
        issuer_sha256=hashlib.sha256(ISSUER.encode()).hexdigest(),
        evidence_mode="clerk_404_resource_not_found",
    )
    get.assert_called_once_with(user_id=SUBJECT, retries=None, timeout_ms=6000)


@pytest.mark.parametrize(
    "environment", ["development", "test", "staging", "production", " STAGING "]
)
def test_allowed_runtime_normalization(probe, monkeypatch, environment):
    monkeypatch.setattr(configuration, "python_environment", lambda: environment)
    result = configuration.prove_absent_owner_identity(SUBJECT)
    assert result is not None and result.environment == environment.strip().lower()


@pytest.mark.parametrize(
    "subject",
    [
        None,
        1,
        "",
        "user_",
        "user_has_underscore",
        "user_bad-identity",
        " user_Valid",
        "user_" + "a" * 129,
    ],
)
def test_invalid_exact_subject_never_calls_clerk(probe, subject):
    get, _ = probe
    assert configuration.prove_absent_owner_identity(subject) is None
    get.assert_not_called()


@pytest.mark.parametrize("status", [200, 400, 401, 403, 429, 500])
def test_other_http_status_never_proves_absence(probe, status):
    get, _ = probe
    get.side_effect = missing_error(status)
    assert configuration.prove_absent_owner_identity(SUBJECT) is None
    assert get.call_count == 1


@pytest.mark.parametrize(
    "codes",
    [
        (),
        ("other",),
        ("resource_not_found", "resource_not_found"),
        ("resource_not_found", "other"),
    ],
)
def test_only_one_exact_error_code_is_evidence(probe, codes):
    get, _ = probe
    get.side_effect = missing_error(codes=codes)
    assert configuration.prove_absent_owner_identity(SUBJECT) is None


@pytest.mark.parametrize(
    "response",
    [
        None,
        SimpleNamespace(),
        SimpleNamespace(id=SUBJECT),
        {"errors": [{"code": "resource_not_found"}]},
    ],
)
def test_returned_objects_including_none_are_not_absence(probe, response):
    get, _ = probe
    get.side_effect = None
    get.return_value = response
    assert configuration.prove_absent_owner_identity(SUBJECT) is None
    assert get.call_count == 1


def test_untyped_lookalike_and_generic_sdk_404_are_not_absence(probe):
    get, _ = probe
    lookalike = RuntimeError("synthetic private detail")
    lookalike.status_code = 404
    lookalike.data = SimpleNamespace(
        errors=[SimpleNamespace(code="resource_not_found")]
    )
    for error in (
        lookalike,
        SDKError("synthetic", httpx.Response(404, json={})),
        httpx.ConnectError("synthetic"),
        httpx.ReadTimeout("synthetic"),
    ):
        get.reset_mock()
        get.side_effect = error
        assert configuration.prove_absent_owner_identity(SUBJECT) is None
        assert get.call_count == 1


@pytest.mark.parametrize(
    "malformation",
    [
        "status_string",
        "status_boolean",
        "data_none",
        "data_untyped",
        "errors_none",
        "entry_untyped",
    ],
)
def test_malformed_typed_errors_are_not_absence(probe, malformation):
    get, _ = probe
    error = missing_error()
    if malformation == "status_string":
        error.status_code = "404"
    elif malformation == "status_boolean":
        error.status_code = True
    elif malformation == "data_none":
        error.data = None
    elif malformation == "data_untyped":
        error.data = SimpleNamespace(
            errors=[SimpleNamespace(code="resource_not_found")]
        )
    elif malformation == "errors_none":
        error.data = ClerkErrorsData.model_construct(errors=None)
    else:
        error.data = ClerkErrorsData.model_construct(
            errors=[SimpleNamespace(code="resource_not_found")]
        )
    get.side_effect = error
    assert configuration.prove_absent_owner_identity(SUBJECT) is None


@pytest.mark.parametrize("environment", ["", "unknown", "preview", None])
def test_invalid_runtime_refuses_before_request(probe, monkeypatch, environment):
    get, _ = probe
    monkeypatch.setattr(configuration, "python_environment", lambda: environment)
    assert configuration.prove_absent_owner_identity(SUBJECT) is None
    get.assert_not_called()


@pytest.mark.parametrize(
    "issuer",
    [
        "",
        "http://synthetic.invalid",
        "https://synthetic.invalid/",
        "https://synthetic.invalid/path",
        "https://name:password@synthetic.invalid",
    ],
)
def test_invalid_configured_issuer_refuses_before_request(probe, monkeypatch, issuer):
    get, settings = probe
    monkeypatch.setattr(
        authority, "AUTHORITY_KERNEL_SETTINGS", replace(settings, clerk_issuer=issuer)
    )
    assert configuration.prove_absent_owner_identity(SUBJECT) is None
    get.assert_not_called()


@pytest.mark.parametrize("drift", ["environment", "issuer", "issuer_invalid", "client"])
def test_provenance_and_configured_client_must_remain_unchanged(
    probe, monkeypatch, drift
):
    get, settings = probe

    def mutate_then_absent(**kwargs):
        assert kwargs == {"user_id": SUBJECT, "retries": None, "timeout_ms": 6000}
        if drift == "environment":
            monkeypatch.setattr(
                configuration, "python_environment", lambda: "production"
            )
        elif drift in {"issuer", "issuer_invalid"}:
            issuer = "https://changed.clerk.invalid" if drift == "issuer" else ""
            monkeypatch.setattr(
                authority,
                "AUTHORITY_KERNEL_SETTINGS",
                replace(settings, clerk_issuer=issuer),
            )
        else:
            monkeypatch.setattr(configuration, "clerk", SimpleNamespace())
        raise missing_error()

    get.side_effect = mutate_then_absent
    assert configuration.prove_absent_owner_identity(SUBJECT) is None
    assert get.call_count == 1
