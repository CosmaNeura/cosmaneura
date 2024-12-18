from pathlib import Path

from azure.storage.blob import BlobServiceClient

service = BlobServiceClient(
    account_url="https://<my-storage-account-name>.blob.core.windows.net/",
    credential=credential,
)

from datetime import datetime, timedelta

from azure.storage.blob import (
    AccountSasPermissions,
    BlobServiceClient,
    ResourceTypes,
    generate_account_sas,
)

sas_token = generate_account_sas(
    # account_name="cosmaneuraproduploads",
    account_name="telehealthcosmaneura",
    account_key="SECRET",
    resource_types=ResourceTypes(service=True),
    permission=AccountSasPermissions(read=True),
    expiry=datetime.utcnow() + timedelta(hours=1),
)

blob_service_client = BlobServiceClient(
    account_url="https://cosmaneuraproduploads.blob.core.windows.net/",
    credential=sas_token,
)


def upload_file_with_sas(file_path, sas_url):
    # Append `&comp=blockblob` if it's not already in the SAS URL
    if "&comp=blockblob" not in sas_url:
        sas_url += "&comp=blockblob"

    with open(file_path, "rb") as file:
        # Send PUT request to upload the file
        headers = {
            "x-ms-blob-type": "BlockBlob",  # Required header
        }
        response = requests.put(sas_url, headers=headers, data=file)

    if response.status_code == 201:
        print("File uploaded successfully!")
    else:
        print(f"Failed to upload file: {response.status_code}")
        print(response.text)


from azure.storage.blob import BlobClient

# only blob permission is required
blob = BlobClient.from_connection_string(
    # conn_str="https://telehealthcosmaneura.blob.core.windows.net/?sv=2022-11-02&ss=bfqt&srt=c&sp=rwdlacupiytfx&se=2024-12-17T03:48:46Z&st=2024-12-16T19:48:46Z&spr=https&sig=TDmTatJSKg4ucrcFxUPqTgh3Odr9OvNjqLnN5thxTTw%3D",
    conn_str="BlobEndpoint=https://telehealthcosmaneura.blob.core.windows.net/;QueueEndpoint=https://telehealthcosmaneura.queue.core.windows.net/;FileEndpoint=https://telehealthcosmaneura.file.core.windows.net/;TableEndpoint=https://telehealthcosmaneura.table.core.windows.net/;SharedAccessSignature=sv=2022-11-02&ss=bfqt&srt=o&sp=rwdlacupiytfx&se=2024-12-17T03:56:47Z&st=2024-12-16T19:56:47Z&spr=https&sig=5kYZcjfVNb5H8UPbAjXCTNxcxuEeYH8QouBBZh9vOaM%3D",
    container_name="telehealth-recording",
    blob_name="hello2.txt",
)

blob.upload_blob(Path("hello.txt").read_bytes())

with open("./hello.txt", "rb") as data:
    blob.upload_blob(data)

# Example Usage
file_path = "path/to/your/file.txt"
sas_url = "https://<yourstorageaccount>.blob.core.windows.net/<yourcontainer>/<filename>?<sas_token>"
upload_file_with_sas(file_path, sas_url)
