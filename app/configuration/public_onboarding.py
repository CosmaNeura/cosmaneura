from decouple import config

from app.configuration.stripe import stripe_billing_configured

# The public self-serve lane is registered dark. Nothing about it is reachable
# until this is deliberately enabled in a deployment, which keeps every
# incremental slice inert while the flow is still being assembled.
PUBLIC_ONBOARDING_ENABLED = config(
    "PUBLIC_ONBOARDING_ENABLED", default=False, cast=bool
)


def public_onboarding_configured() -> bool:
    """Whether the public self-serve onboarding surface may be served.

    Composed with Stripe acquisition readiness on purpose: the lane terminates
    at hosted Checkout with a card (ADR 0024), so serving it from a deployment
    that cannot create a subscription would advertise a flow that cannot
    complete.
    """

    return bool(PUBLIC_ONBOARDING_ENABLED and stripe_billing_configured())
