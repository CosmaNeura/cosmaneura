"""Intake is default-off; the migrated ledger fence survives intake shutdown."""

from decouple import config

from app.configuration.scribe_native import SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS
from app.services.scribe_cancellation_contract import (
    CancellationPolicy,
    CancellationSettings,
)


def load_cancellation_settings() -> CancellationSettings:
    ledger_ready = config("SCRIBE_CANCELLATION_LEDGER_READY", default=False, cast=bool)
    if not config("SCRIBE_CANCELLATION_ENABLED", default=False, cast=bool):
        return CancellationSettings(ledger_ready=ledger_ready)
    if not ledger_ready:
        raise ValueError("scribe_cancellation_requires_migrated_ledger")
    if not SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS.enabled:
        raise ValueError("scribe_cancellation_requires_native_workload")
    policy = CancellationPolicy.model_validate(
        {
            "grace_days": config(
                "SCRIBE_CANCELLATION_GRACE_DAYS", default=90, cast=int
            ),
            "phi_scope": config(
                "SCRIBE_CANCELLATION_PHI_SCOPE",
                default="core-and-scribe-clinical-records",
                cast=str,
            ),
            "approval_sha256": config("SCRIBE_CANCELLATION_POLICY_SHA256", cast=str),
        }
    )
    return CancellationSettings(enabled=True, policy=policy, ledger_ready=True)


SCRIBE_CANCELLATION_SETTINGS = load_cancellation_settings()
