from azure.storage.blob import BlobServiceClient

from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_URL,
)


def download_blob(filename: str, container_name: str):
    # TODO should we restrict container_name to valid entries like `AZURE_UPLOADS_CONTAINER_NAME`

    service_client = BlobServiceClient(
        AZURE_UPLOADS_ACCOUNT_URL,
        credential=AZURE_UPLOADS_ACCOUNT_KEY,
    )

    blob_client = service_client.get_blob_client(
        container=container_name,
        blob=filename,
    )

    return blob_client.download_blob().readall()
