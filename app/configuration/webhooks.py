import base64
import binascii

from decouple import config

_MIN_RECALL_SIGNING_SECRET_BYTES = 16


def validate_recall_webhook_secret(value: str) -> str:
    """Validate a configured Recall/Svix key without disclosing its value."""

    if value == "":
        if config("PYTHON_ENV", default="development", cast=str).lower() in {
            "production",
            "staging",
        }:
            raise ValueError("RECALL_WEBHOOK_SECRET is required for this environment")
        return value
    if value != value.strip() or not value.startswith("whsec_"):
        raise ValueError("RECALL_WEBHOOK_SECRET has invalid signing-key syntax")
    payload = value.removeprefix("whsec_")
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(
            "RECALL_WEBHOOK_SECRET has invalid signing-key syntax"
        ) from None
    if (
        len(decoded) < _MIN_RECALL_SIGNING_SECRET_BYTES
        or base64.b64encode(decoded).decode("ascii") != payload
    ):
        raise ValueError("RECALL_WEBHOOK_SECRET has invalid signing-key syntax")
    return value


# Recall/Svix webhook signing secrets are endpoint-specific credentials for
# older Recall workspaces and workspace-wide credentials for newer ones.  The
# application deliberately treats the value as an opaque API-only secret.
RECALL_WEBHOOK_SECRET = config(
    "RECALL_WEBHOOK_SECRET", default="", cast=validate_recall_webhook_secret
)

# Deployment starts on the compatible signed legacy queue path. Operators
# enable direct commit-before-publish ingress only after the migration, worker,
# scheduler and health checks are present and legacy Redis work has drained
# (docs/RECALL_WEBHOOK_INBOX_RUNBOOK.md).
DURABLE_WEBHOOK_INBOX_ENABLED = config(
    "DURABLE_WEBHOOK_INBOX_ENABLED", default=False, cast=bool
)
