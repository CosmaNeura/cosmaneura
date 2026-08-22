import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from app.jobs.anonymize import deidentify


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
