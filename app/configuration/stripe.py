from decouple import config

# The integration and new-checkout rollout are deliberately separate. Once a
# customer exists, disabling acquisition must not disable lifecycle webhooks,
# the billing portal, or quantity reconciliation for that customer.
STRIPE_INTEGRATION_ENABLED = config(
    "STRIPE_INTEGRATION_ENABLED", default=False, cast=bool
)
STRIPE_BILLING_ENABLED = config("STRIPE_BILLING_ENABLED", default=False, cast=bool)
STRIPE_SECRET_KEY = config("STRIPE_SECRET_KEY", default="", cast=str)
STRIPE_ACCOUNT_ID = config("STRIPE_ACCOUNT_ID", default="", cast=str)
STRIPE_PRICE_ID = config("STRIPE_PRICE_ID", default="", cast=str)
STRIPE_PORTAL_CONFIGURATION_ID = config(
    "STRIPE_PORTAL_CONFIGURATION_ID", default="", cast=str
)
STRIPE_WEBHOOK_SECRET = config("STRIPE_WEBHOOK_SECRET", default="", cast=str)
STRIPE_API_VERSION = config("STRIPE_API_VERSION", default="", cast=str)
STRIPE_LIVE_MODE = config("STRIPE_LIVE_MODE", default=False, cast=bool)

# Confirmed against the live account default and existing endpoint on
# 2026-08-20. A mutable environment value alone must never select the contract.
SUPPORTED_STRIPE_API_VERSION = "2025-09-30.clover"


def _secret_mode_matches() -> bool:
    expected = (
        ("sk_live_", "rk_live_")
        if STRIPE_LIVE_MODE
        else (
            "sk_test_",
            "rk_test_",
        )
    )
    return STRIPE_SECRET_KEY.startswith(expected)


def stripe_runtime_configured() -> bool:
    """Whether authenticated Stripe maintenance calls can run."""

    return bool(
        STRIPE_INTEGRATION_ENABLED
        and _secret_mode_matches()
        and STRIPE_ACCOUNT_ID.startswith("acct_")
        and STRIPE_PRICE_ID.startswith("price_")
        and SUPPORTED_STRIPE_API_VERSION
        and STRIPE_API_VERSION == SUPPORTED_STRIPE_API_VERSION
    )


def stripe_webhook_configured() -> bool:
    """Whether lifecycle events can be verified and reconciled."""

    return bool(
        stripe_runtime_configured() and STRIPE_WEBHOOK_SECRET.startswith("whsec_")
    )


def stripe_billing_configured() -> bool:
    """Whether creating *new* Checkout subscriptions is safe.

    Webhook readiness is part of acquisition readiness: we never sell a
    subscription that this deployment cannot subsequently mirror.
    """

    return bool(STRIPE_BILLING_ENABLED and stripe_webhook_configured())
