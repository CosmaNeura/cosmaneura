from pathlib import Path

from app.configuration.scribe_workflow import (
    SCRIBE_SERVICE_TIMEOUT_SECONDS_LIMIT,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_terraform_and_application_timeout_defaults_remain_aligned() -> None:
    variables = (REPOSITORY_ROOT / "infra/variables.tf").read_text()
    timeout_block = variables.split(
        'variable "scribe_service_timeout_seconds" {', maxsplit=1
    )[1].split("\n}", maxsplit=1)[0]

    assert SCRIBE_SERVICE_TIMEOUT_SECONDS_LIMIT == 60
    assert "default     = 60" in timeout_block
    assert "var.scribe_service_timeout_seconds <= 60" in timeout_block


def test_capture_admission_allows_transport_budget_before_lease_expiry() -> None:
    from app.services.scribe_capture_admission import (
        LEASE_SECONDS,
        PHASE_TIMEOUT_SECONDS,
    )

    assert SCRIBE_SERVICE_TIMEOUT_SECONDS_LIMIT < PHASE_TIMEOUT_SECONDS < LEASE_SECONDS
    assert LEASE_SECONDS <= 120
