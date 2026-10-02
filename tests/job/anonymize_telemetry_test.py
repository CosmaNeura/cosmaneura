import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from app.jobs.anonymize import deidentify


def test_deidentification_uses_exact_user_assigned_identity() -> None:
    managed_identity_client_id = "11111111-1111-1111-1111-111111111111"

    with (
        patch("app.jobs.anonymize.AZURE_DEID_AUTH_MODE", "managed_identity"),
        patch(
            "app.jobs.anonymize.AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID",
            managed_identity_client_id,
        ),
        patch("app.jobs.anonymize.ClientSecretCredential") as client_secret,
        patch("app.jobs.anonymize.ManagedIdentityCredential") as managed_identity,
        patch("app.jobs.anonymize.DeidentificationClient") as client_class,
    ):
        client_class.return_value.deidentify.return_value = SimpleNamespace(
            output_text="Person ABC has a condition."
        )
        deidentify("Patient Jane Private has a private diagnosis.")

    managed_identity.assert_called_once_with(client_id=managed_identity_client_id)
    client_secret.assert_not_called()


def test_deidentification_legacy_mode_remains_available_for_rollback() -> None:
    with (
        patch("app.jobs.anonymize.AZURE_DEID_AUTH_MODE", "client_secret"),
        patch("app.jobs.anonymize.ClientSecretCredential") as client_secret,
        patch("app.jobs.anonymize.ManagedIdentityCredential") as managed_identity,
        patch("app.jobs.anonymize.DeidentificationClient") as client_class,
    ):
        client_class.return_value.deidentify.return_value = SimpleNamespace(
            output_text="Person ABC has a condition."
        )
        deidentify("Patient Jane Private has a private diagnosis.")

    client_secret.assert_called_once()
    managed_identity.assert_not_called()


def test_deidentification_logs_neither_input_nor_output() -> None:
    input_text = "Patient Jane Private has a private diagnosis."
    output_text = "Person ABC has a condition."

    with (
        patch("app.jobs.anonymize.ClientSecretCredential"),
        patch("app.jobs.anonymize.DeidentificationClient") as client_class,
    ):
        client_class.return_value.deidentify.return_value = SimpleNamespace(
            output_text=output_text
        )
        with capture_logs() as logs:
            result = deidentify(input_text)

    assert result == output_text
    serialized = json.dumps(logs, sort_keys=True)
    assert input_text not in serialized
    assert output_text not in serialized
    assert [entry.get("event_code") for entry in logs] == [
        "deidentification_request_started",
        "deidentification_request_completed",
    ]
    assert {entry.get("credential_mode") for entry in logs} == {"client_secret"}


def test_deidentification_logs_no_provider_exception_details() -> None:
    input_text = "Patient Private Input Sentinel"
    provider_error = "https://provider.invalid/private?patient=Clinical+Sentinel"

    with (
        patch("app.jobs.anonymize.ClientSecretCredential"),
        patch("app.jobs.anonymize.DeidentificationClient") as client_class,
    ):
        client_class.return_value.deidentify.side_effect = RuntimeError(provider_error)
        with (
            capture_logs() as logs,
            pytest.raises(RuntimeError, match="provider.invalid"),
        ):
            deidentify(input_text)

    serialized = json.dumps(logs, sort_keys=True)
    assert input_text not in serialized
    assert provider_error not in serialized
    assert logs[-1]["event_code"] == "deidentification_request_failed"
    assert logs[-1]["credential_mode"] == "client_secret"


def test_optional_bounded_call_preserves_core_identity_and_stable_operation():
    with (
        patch("app.jobs.anonymize.ClientSecretCredential"),
        patch("app.jobs.anonymize.DeidentificationClient") as client_class,
    ):
        client_class.return_value.deidentify.return_value = SimpleNamespace(
            output_text="Invented protected words"
        )
        assert (
            deidentify("Invented source", timeout_seconds=12.0)
            == "Invented protected words"
        )
    options = client_class.call_args.kwargs
    assert options["api_version"] == "2024-11-15"
    assert options["retry_total"] == 0
    assert options["connection_timeout"] == 3.0 and options["read_timeout"] == 12.0
    assert options["logging_enable"] is False
    args = client_class.return_value.deidentify.call_args
    assert args.args[0].operation == "Surrogate"
    assert args.kwargs["retry_total"] == 0 and args.kwargs["timeout"] == 12.0


@pytest.mark.parametrize("timeout", [0, -1, 16, True, float("nan"), float("inf"), "12"])
def test_invalid_bound_is_rejected_before_any_credential_or_request(timeout):
    with (
        patch("app.jobs.anonymize.ClientSecretCredential") as credential,
        patch("app.jobs.anonymize.ManagedIdentityCredential") as identity,
        patch("app.jobs.anonymize.DeidentificationClient") as client,
        pytest.raises(ValueError, match="Invalid deidentification timeout"),
    ):
        deidentify("Invented source", timeout_seconds=timeout)
    credential.assert_not_called()
    identity.assert_not_called()
    client.assert_not_called()
