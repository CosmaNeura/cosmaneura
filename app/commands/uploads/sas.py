from datetime import datetime, timedelta, timezone
from enum import Enum

from azure.storage.blob import (
    AccountSasPermissions,
    ContainerClient,
    ResourceTypes,
    generate_account_sas,
)

from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_NAME,
    AZURE_UPLOADS_ACCOUNT_URL,
    AZURE_UPLOADS_CONTAINER_NAME,
    AZURE_UPLOADS_EXAMPLE_INTAKE_CONTAINER_NAME,
    AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
)


class UploadType(Enum):
    VISIT = AZURE_UPLOADS_CONTAINER_NAME
    EXAMPLE_NOTE = AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME
    EXAMPLE_INTAKE = AZURE_UPLOADS_EXAMPLE_INTAKE_CONTAINER_NAME


def perform(upload_type: UploadType) -> str:
    """
    Generate a SAS token for uploading files, like audio, to a azure storage blob.

    * You'll get 'The specified container does not exist.' if you don't specify a filename in the path
    * "Object" is the name used for blob storage
    """

    sas_token = generate_account_sas(
        account_name=AZURE_UPLOADS_ACCOUNT_NAME,
        account_key=AZURE_UPLOADS_ACCOUNT_KEY,
        resource_types=ResourceTypes(object=True),
        permission=AccountSasPermissions(write=True),
        # 1hr is arbitrary
        expiry=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    container_client = ContainerClient(
        container_name=upload_type.value,
        account_url=AZURE_UPLOADS_ACCOUNT_URL,
        credential=sas_token,
    )

    return container_client.url
