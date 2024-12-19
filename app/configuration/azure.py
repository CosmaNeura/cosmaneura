from datetime import datetime, timedelta
from pathlib import Path

from azure.storage.blob import (
    AccountSasPermissions,
    BlobServiceClient,
    ContainerClient,
    ResourceTypes,
    generate_account_sas,
)
from decouple import config

AZURE_UPLOADS_ACCOUNT_NAME = config("AZURE_UPLOADS_ACCOUNT_NAME", cast=str)
AZURE_UPLOADS_ACCOUNT_KEY = config("AZURE_UPLOADS_ACCOUNT_KEY", cast=str)
AZURE_UPLOADS_ACCOUNT_URL = config("AZURE_UPLOADS_ACCOUNT_URL", cast=str)
AZURE_UPLOADS_CONTAINER_NAME = config("AZURE_UPLOADS_CONTAINER_NAME", cast=str)


def configure_azure():
    pass
