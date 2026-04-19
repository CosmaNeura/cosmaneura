#!/usr/bin/env python
"""Debug Azure Blob Storage connectivity and list blobs in a container."""

from __future__ import annotations

import argparse
import os

from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
)
from azure.storage.blob import BlobServiceClient


def _read_config() -> tuple[str | None, str | None, str | None]:
    """
    Read Azure settings from app config first, then fallback to environment variables.
    """
    account_url = account_key = container_name = None

    try:
        from app.configuration.azure import (
            AZURE_UPLOADS_ACCOUNT_KEY,
            AZURE_UPLOADS_ACCOUNT_URL,
            AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
        )

        account_url = AZURE_UPLOADS_ACCOUNT_URL
        account_key = AZURE_UPLOADS_ACCOUNT_KEY
        container_name = AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME
    except Exception:
        pass

    account_url = account_url or os.getenv("AZURE_UPLOADS_ACCOUNT_URL")
    account_key = account_key or os.getenv("AZURE_UPLOADS_ACCOUNT_KEY")
    container_name = container_name or os.getenv(
        "AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME"
    )

    return account_url, account_key, container_name


def _mask_secret(secret: str | None) -> str:
    if not secret:
        return "<missing>"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debug Azure Blob connection and list blobs in a container."
    )
    parser.add_argument(
        "--container",
        default=None,
        help="Container name. Defaults to AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME.",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Optional blob name prefix filter.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum number of blobs to print (default: 200).",
    )
    parser.add_argument(
        "--check-blob",
        default=None,
        help="Optional blob name to verify existence and print properties.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    account_url, account_key, default_container = _read_config()
    container_name = args.container or default_container

    print("Azure Blob debug")
    print(f"  account_url: {account_url!r}")
    print(f"  account_key: {_mask_secret(account_key)!r}")
    print(f"  container:   {container_name!r}")
    print(f"  prefix:      {args.prefix!r}")
    print(f"  limit:       {args.limit!r}")

    if not account_url or not account_key or not container_name:
        print(
            "\nMissing config. Ensure these are defined:\n"
            "  - AZURE_UPLOADS_ACCOUNT_URL\n"
            "  - AZURE_UPLOADS_ACCOUNT_KEY\n"
            "  - AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME (or pass --container)"
        )
        return 2

    try:
        service_client = BlobServiceClient(account_url, credential=account_key)
        container_client = service_client.get_container_client(container_name)

        # Lightweight request to validate access and container existence.
        container_props = container_client.get_container_properties()
        print(
            f"\nConnected successfully. Last modified: {container_props.last_modified!s}"
        )

        if args.check_blob:
            blob_client = container_client.get_blob_client(args.check_blob)
            exists = blob_client.exists()
            print(f"Blob exists [{args.check_blob!r}]: {exists}")
            if exists:
                props = blob_client.get_blob_properties()
                print(f"  size_bytes: {props.size}")
                print(f"  content_type: {props.content_settings.content_type!r}")
                print(f"  last_modified: {props.last_modified!s}")

        print("\nBlobs:")
        count = 0
        for blob in container_client.list_blobs(name_starts_with=args.prefix):
            print(f"  {blob.name!r}")
            count += 1
            if count >= args.limit:
                print(f"  ... truncated after {args.limit} blobs")
                break
        print(f"\nTotal listed: {count}")
        return 0

    except ClientAuthenticationError as exc:
        print(f"\nAuthentication failed: {exc}")
        return 1
    except ResourceNotFoundError as exc:
        print(f"\nContainer or blob not found: {exc}")
        return 1
    except ServiceRequestError as exc:
        print(f"\nNetwork/service request error: {exc}")
        return 1
    except HttpResponseError as exc:
        print(f"\nAzure HTTP error: {exc}")
        return 1
    except Exception as exc:
        print(f"\nUnexpected error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
