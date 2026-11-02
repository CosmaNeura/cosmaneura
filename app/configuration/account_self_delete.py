from decouple import config

# Server-side latch for DELETE /v1/me/account. Defaults OFF in every
# environment; staging turns it on deliberately through non-secret config.
ACCOUNT_SELF_DELETE_ENABLED = config(
    "ACCOUNT_SELF_DELETE_ENABLED", default=False, cast=bool
)
