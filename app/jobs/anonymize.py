import os
import time
from azure.identity import ClientSecretCredential
from azure.health.deidentification import DeidentificationClient
from azure.health.deidentification.models import (
        DeidentificationResult,
        DeidentificationContent,
        OperationType,
        DocumentDataType,
        )

from app.configuration.azure import (
    AZURE_DEID_TENANT_ID,
    AZURE_DEID_CLIENT_ID,
    AZURE_DEID_CLIENT_SECRET,
    AZURE_DEID_ENDPOINT,
)

# https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/healthdataaiservices/azure-health-deidentification/samples/sample_realtime_deidentification.py
def deidentify(phi_text: str) -> str:
    # The Deidentification Service requires a service principal to authenticate.
    # Create one through the Entra portal.
    #   https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal
    # Make sure that your application has the "DeID Data Owner" role.
    credential = ClientSecretCredential(
        client_id=AZURE_DEID_CLIENT_ID,
        tenant_id=AZURE_DEID_TENANT_ID,
        client_secret=AZURE_DEID_CLIENT_SECRET,
    )

    # The deid service configuration JSON says that it uses API version
    # "2024-09-20, but the SDK throws an error if you specify it.
    # client = DeidentificationClient(endpoint, credential, api_version="2024-09-20")
    client = DeidentificationClient(AZURE_DEID_ENDPOINT, credential)
    body = DeidentificationContent(
        input_text=phi_text,
        operation="Surrogate"
    )

    result: DeidentificationResult = client.deidentify(body)
    assert result.output_text
    print(f'Deidentified Text: "{result.output_text}"')

    return result.output_text
