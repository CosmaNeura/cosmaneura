from uuid import UUID

from decouple import config

AZURE_UPLOADS_ACCOUNT_NAME = config("AZURE_UPLOADS_ACCOUNT_NAME", cast=str)
AZURE_UPLOADS_ACCOUNT_KEY = config("AZURE_UPLOADS_ACCOUNT_KEY", cast=str)
AZURE_UPLOADS_ACCOUNT_URL = config("AZURE_UPLOADS_ACCOUNT_URL", cast=str)
AZURE_UPLOADS_CONTAINER_NAME = config("AZURE_UPLOADS_CONTAINER_NAME", cast=str)
AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME = config(
    "AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME", cast=str
)

AZURE_DEID_AUTH_MODE = (
    config("AZURE_DEID_AUTH_MODE", default="client_secret", cast=str).strip().lower()
)
if AZURE_DEID_AUTH_MODE not in {"client_secret", "managed_identity"}:
    raise ValueError("AZURE_DEID_AUTH_MODE must be client_secret or managed_identity")

# The legacy app registration remains available only as a controlled rollback
# while the production worker moves to its user-assigned managed identity. In
# managed-identity mode these values are deliberately optional so the old
# credential can be removed after the observation window.
if AZURE_DEID_AUTH_MODE == "managed_identity":
    AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID = config(
        "AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID", cast=str
    ).strip()
    try:
        if (
            str(UUID(AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID))
            != AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID.lower()
        ):
            raise ValueError
    except ValueError as error:
        raise ValueError(
            "AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID must be a canonical UUID"
        ) from error
    AZURE_DEID_TENANT_ID = config("AZURE_DEID_TENANT_ID", default="", cast=str)
    AZURE_DEID_CLIENT_ID = config("AZURE_DEID_CLIENT_ID", default="", cast=str)
    AZURE_DEID_CLIENT_SECRET = config("AZURE_DEID_CLIENT_SECRET", default="", cast=str)
else:
    AZURE_DEID_TENANT_ID = config("AZURE_DEID_TENANT_ID", cast=str)
    AZURE_DEID_CLIENT_ID = config("AZURE_DEID_CLIENT_ID", cast=str)
    AZURE_DEID_CLIENT_SECRET = config("AZURE_DEID_CLIENT_SECRET", cast=str)
    AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID = config(
        "AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID", default="", cast=str
    )
AZURE_DEID_ENDPOINT = config("AZURE_DEID_ENDPOINT", cast=str)


def configure_azure():
    # TODO we should configure the logging, it's pretty noisy
    pass
