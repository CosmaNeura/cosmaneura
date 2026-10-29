#!/usr/bin/env python
"""Check Azure Blob connectivity without printing credentials or identifiers."""

from __future__ import annotations

import argparse
import os
from typing import NoReturn

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


class _RedactedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default errors echo unrecognized arguments and invalid values.
        self.exit(2, "Invalid arguments. Use --help for usage.\n")


def _configuration_status(value: str | None) -> str:
    return "configured" if value else "missing"


def parse_args() -> argparse.Namespace:
    parser = _RedactedArgumentParser(
        description="Check Azure Blob connection and count matching blobs without logging identifiers."
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
        help="Optional blob name to verify existence and property access; values are not printed.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        account_url, account_key, default_container = _read_config()
        container_name = args.container or default_container

        print("Azure Blob debug")
        print(f"  account_url: {_configuration_status(account_url)}")
        print(f"  account_key: {_configuration_status(account_key)}")
        print(f"  container: {_configuration_status(container_name)}")

        if not account_url or not account_key or not container_name:
            print(
                "\nMissing config. Ensure these are defined:\n"
                "  - AZURE_UPLOADS_ACCOUNT_URL\n"
                "  - AZURE_UPLOADS_ACCOUNT_KEY\n"
                "  - AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME (or pass --container)"
            )
            return 2

        service_client = BlobServiceClient(account_url, credential=account_key)
        container_client = service_client.get_container_client(container_name)

        # Lightweight request to validate access and container existence.
        container_client.get_container_properties()
        print("\nConnected successfully.")

        if args.check_blob:
            blob_client = container_client.get_blob_client(args.check_blob)
            exists = bool(blob_client.exists())
            print(f"Blob exists: {exists}")
            if exists:
                blob_client.get_blob_properties()
                print("Blob properties: readable")

        count = 0
        for _ in container_client.list_blobs(name_starts_with=args.prefix):
            count += 1
            if count >= args.limit:
                print(f"  ... truncated after {args.limit} blobs")
                break
        print(f"\nTotal listed: {count}")
        return 0

    except ClientAuthenticationError:
        print("\nAuthentication failed")
        return 1
    except ResourceNotFoundError:
        print("\nContainer or blob not found")
        return 1
    except ServiceRequestError:
        print("\nNetwork/service request error")
        return 1
    except HttpResponseError:
        print("\nAzure HTTP error")
        return 1
    except Exception:
        print("\nUnexpected error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
