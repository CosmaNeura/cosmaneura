from decouple import config

AZURE_UPLOADS_ACCOUNT_NAME = config("AZURE_UPLOADS_ACCOUNT_NAME", cast=str)
AZURE_UPLOADS_ACCOUNT_KEY = config("AZURE_UPLOADS_ACCOUNT_KEY", cast=str)
AZURE_UPLOADS_ACCOUNT_URL = config("AZURE_UPLOADS_ACCOUNT_URL", cast=str)
AZURE_UPLOADS_CONTAINER_NAME = config("AZURE_UPLOADS_CONTAINER_NAME", cast=str)
AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME = config(
    "AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME", cast=str
)

# these credentials are all manually managed
# https://github.com/Azure/azure-sdk-for-python/pull/38437/files#diff-4a6cd46fa852c722a5da15b423ecb2134efc9a12c0629e14924dc15544196e18R20-R23
# it will automatically expire after some amount of time (6mo-2yrs). You'll have to generate a new one here:
# https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Credentials/appId/af957741-0342-498b-8496-e93706af9e99/isMSAApp~/false

AZURE_DEID_TENANT_ID = config("AZURE_DEID_TENANT_ID", cast=str)
AZURE_DEID_CLIENT_ID = config("AZURE_DEID_CLIENT_ID", cast=str)
AZURE_DEID_CLIENT_SECRET = config("AZURE_DEID_CLIENT_SECRET", cast=str)
AZURE_DEID_ENDPOINT = config("AZURE_DEID_ENDPOINT", cast=str)


def configure_azure():
    # TODO we should configure the logging, it's pretty noisy
    pass
