from decouple import config

# Optional at import time: billing endpoints return 503 until these are set,
# so staging/test environments run without Stripe credentials.
STRIPE_SECRET_KEY = config("STRIPE_SECRET_KEY", default="", cast=str)
STRIPE_PRICE_ID = config("STRIPE_PRICE_ID", default="", cast=str)
STRIPE_WEBHOOK_SECRET = config("STRIPE_WEBHOOK_SECRET", default="", cast=str)


def stripe_billing_configured() -> bool:
    return bool(STRIPE_SECRET_KEY and STRIPE_PRICE_ID)
