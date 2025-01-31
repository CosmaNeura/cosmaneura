from azure.health.deidentification import DeidentificationClient
from azure.health.deidentification.models import (
    DeidentificationContent,
    DeidentificationResult,
)
from azure.identity import ClientSecretCredential

from app import log
from app.configuration.azure import (
    AZURE_DEID_CLIENT_ID,
    AZURE_DEID_CLIENT_SECRET,
    AZURE_DEID_ENDPOINT,
    AZURE_DEID_TENANT_ID,
)


# https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/healthdataaiservices/azure-health-deidentification/samples/sample_realtime_deidentification.py
def deidentify(phi_text: str) -> str:
    text = phi_text.strip()
    if not text:
        return text

    # The Deidentification Service requires a service principal to authenticate.
    # Create one through the Entra portal.
    #   https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal
    # Make sure that your application has the "DeID Data Owner" role.
    credential = ClientSecretCredential(
        client_id=AZURE_DEID_CLIENT_ID,
        tenant_id=AZURE_DEID_TENANT_ID,
        client_secret=AZURE_DEID_CLIENT_SECRET,
    )

    log.info("running deid request")

    # The deid service configuration JSON says that it uses API version
    # "2024-09-20, but the SDK throws an error if you specify it.
    # client = DeidentificationClient(endpoint, credential, api_version="2024-09-20")
    client = DeidentificationClient(AZURE_DEID_ENDPOINT, credential)
    body = DeidentificationContent(input_text=text, operation="Surrogate")

    result: DeidentificationResult
    try:
        result = client.deidentify(body)
        assert result.output_text
    except (Exception) as e:
        # Note that we're deliberately not logging the text here, as it is PHI.
        log.error("error deidentifying text", error=str(e))
        raise

    log.debug("deid Text", output=result.output_text)

    return result.output_text
