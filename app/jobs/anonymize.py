import os
import time
from azure.identity import DefaultAzureCredential
from azure.health.deidentification import DeidentificationClient
from azure.health.deidentification.models import (
        DeidentificationResult,
        DeidentificationContent,
        OperationType,
        DocumentDataType,
        )

# https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/healthdataaiservices/azure-health-deidentification/samples/sample_realtime_deidentification.py
def deidentify(phi_text: str) -> str | None:
    # URL of the Deidentification Service resource.
    # https://portal.azure.com/#@johncosmaneura.onmicrosoft.com/resource/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-prod/providers/Microsoft.HealthDataAIServices/DeidServices/AnonymizerAPI/properties
    endpoint = "fba4aeg2gaf0hwf0.api.eus001.deid.azure.com"

    # The Deidentification Service requires a service principal to authenticate.
    # Create one through the Entra portal.
    #   https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal
    # Make sure that your application has the "DeID Data Owner" role.
    # Entra credentials that need to be set as environment variables:
    #  * AZURE_CLIENT_ID
    #  * AZURE_TENANT_ID
    #  * AZURE_CLIENT_SECRET
    credential = DefaultAzureCredential()

    # The deid service configuration JSON says that it uses API version
    # "2024-09-20, but the SDK throws an error if you specify it.
    # client = DeidentificationClient(endpoint, credential, api_version="2024-09-20")
    client = DeidentificationClient(endpoint, credential)
    body = DeidentificationContent(
        input_text=phi_text,
        operation="Surrogate"
    )

    result: DeidentificationResult = client.deidentify(body)
    print(f'Deidentified Text: "{result.output_text}"')

    return result.output_text

def save_text(text, output_path):
    try:
        with open(output_path, "w") as f:
            f.write(text)
        print(f"Anonymized text saved to {output_path}")

    except Exception as e:
        print(f"Error saving anonymized text: {e}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Anonymize PHI by replacing identifying information with fake data"
    )
    parser.add_argument("file", type=str, help="Path to text file containing PHI")
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as f:
        phi = f.read()

    phi_basename = os.path.basename(args.file)
    phi_filename_no_ext = os.path.splitext(phi_basename)[0]

    anonymized = deidentify(phi)

    output_dir = "./anon"
    os.makedirs(output_dir, exist_ok=True)

    if anonymized:
        timestamp = int(time.time())
        output_filename = f"{output_dir}/{phi_filename_no_ext}_{timestamp}.txt"
        save_text(anonymized, output_filename)
    else:
        print("Failed to generate note.")
