#!/usr/bin/env python3
"""Create or revalidate the one-time non-mutating staging recovery plan."""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import io
import json
import os
import pwd
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REHEARSAL_ROOT = REPOSITORY_ROOT / "infra-staging-rehearsal"
VALIDATOR_PATH = Path(__file__).resolve()

EXPECTED_LINEAGE = "76b6712e-5781-adbd-9fb1-cf66db4f4e28"
EXPECTED_RECOVERY_SERIAL = 9
EXPECTED_FORMAT_VERSION = "1.2"
EXPECTED_TOFU_VERSION = "1.12.5"
EXPECTED_TOFU_PATH = Path("/opt/homebrew/Cellar/opentofu/1.12.5/bin/tofu")
EXPECTED_TOFU_SHA256 = (
    "1334ccf2360fe38460c47fd0924555f411cf714813e541d17d744283c973aa99"
)
EXPECTED_AZURE_CLI_PATH = Path("/opt/homebrew/Cellar/azure-cli/2.89.1/bin/az")
EXPECTED_AZURE_CLI_SHA256 = (
    "e62d2f82b3c4d2a98202154e2a01638b656059e25c25605c56732516527dc6df"
)
EXPECTED_AZURE_CLI_VERSION_SHA256 = (
    "98b3412b818478b3ef731be86fd539b32628e5621342f4873ac325c87428facc"
)
EXPECTED_AZURE_CONFIG_SHA256S = {"config": "b8258f4205b85a305ac3811432073f56fddf4a63b4472485778bfde0f5a69553", "clouds.config": "2189f3b4fe7357a59f5fcdf8f6e35382784aa2862b5262e5dc89d8e615bfc3ed"}  # fmt: skip
AZURE_CONFIG_FILES = ("config", "clouds.config", "azureProfile.json", "msal_token_cache.json")  # fmt: skip
AZURE_CONFIG_BINDING_ENV = "COSMANEURA_AZURE_CONFIG_BINDING_SHA256"
AZURE_CONFIG_LOCK_NAME = ".azure-config.lock"
EXPECTED_AZURE_CLOUD_SHA256 = "1f30818094483f03f402801991b418f9065244acc8c871047ca46e5ce58b872f"  # fmt: skip
EXPECTED_HOMEBREW_ROOT = Path("/opt/homebrew")
EXPECTED_AZURE_CLI_FORMULAE: Mapping[str, str] = {
    "azure-cli": "2.89.1",
    "ca-certificates": "2026-08-13",
    "libsodium": "1.0.22",
    "libyaml": "0.2.5",
    "lz4": "1.10.0",
    "mpdecimal": "4.0.1",
    "openssl@3": "3.6.3",
    "python@3.14": "3.14.7",
    "readline": "8.3.3",
    "sqlite": "3.53.4",
    "xz": "5.8.3",
    "zstd": "1.5.7_1",
}
# Canonical content/type/executable-bit manifest for every file and symlink in
# the Azure CLI Homebrew formula plus its complete non-system runtime closure.
EXPECTED_AZURE_CLI_CLOSURE_SHA256 = (
    "eb13dfaf7731f89ed7bf68591cfc6da4b6df4f27752354db4d3be8caa7049383"
)
EXPECTED_PATCHED_PYTHON_BINARIES: Mapping[str, tuple[int, str]] = {
    (
        "Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/"
        "bin/python3.14"
    ): (
        52_448,
        "e8179bf1ac3710499f164f877a3713bbaa34b5921beb70881f5dcd4c45a17827",
    ),
    (
        "Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/"
        "Resources/Python.app/Contents/MacOS/Python"
    ): (
        51_392,
        "0d6d087edcbab138b3cf861429e6bd66e963f5ca0f3113b2c0a98939d24ea27b",
    ),
}
REJECTED_SAVED_PLAN_SHA256S: frozenset[str] = frozenset(
    {
        # Rejected by independent review on 2026-09-09. This artifact predates
        # the complete runtime, state-projection, and exact no-op bindings and
        # must never regain authorization merely because later code can parse it.
        "2f16bcb6fdeddeca55987084511361f030c7bd5648435d7487461eb540f7ab36",
    }
)
EXPECTED_SUBSCRIPTION_ID = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
EXPECTED_TENANT_ID = "d51754d0-bba5-4810-820e-afac542844d6"
EXPECTED_ORIGIN_URL = "git@github.com:CosmaNeura/cosmaneura.git"
SYSTEM_GIT = Path("/usr/bin/git")
SYSTEM_SSH = Path("/usr/bin/ssh")
GITHUB_HOST_KEY = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
MAXIMUM_ARTIFACT_BYTES = 64 * 1024 * 1024
MAXIMUM_PLAN_JSON_BYTES = 128 * 1024 * 1024
MAXIMUM_PLAN_ARCHIVE_BYTES = 256 * 1024 * 1024
MAXIMUM_EXECUTABLE_BYTES = 128 * 1024 * 1024
MAXIMUM_RUNTIME_CLOSURE_BYTES = 768 * 1024 * 1024
MAXIMUM_RUNTIME_CLOSURE_ENTRIES = 50_000
MAXIMUM_AZURE_CONFIG_FILE_BYTES = 4 * 1024 * 1024
MAXIMUM_PLAN_AGE = timedelta(hours=4)
MAXIMUM_CLOCK_SKEW = timedelta(minutes=5)
TRUSTED_RUNTIME_DIRECTORY_NAME = ".trusted-runtime-bin"

PLAN_VARIABLE_ARGUMENTS = (
    "-var=deploy_staging_restore_control_plane=true",
    "-var=target_cidr_inventory_revalidated=true",
    "-var=control_cidr_inventory_revalidated=true",
)

EXPECTED_EXISTING_ADDRESSES: frozenset[str] = frozenset(
    """
azurerm_federated_identity_credential.dispatcher["cleanup"]
azurerm_federated_identity_credential.dispatcher["provision"]
azurerm_policy_definition.deny_postgres_firewall_rules[0]
azurerm_private_dns_zone.marker_blob[0]
azurerm_private_dns_zone_virtual_network_link.marker_blob[0]
azurerm_resource_group.control[0]
azurerm_resource_group.target[0]
azurerm_resource_group_policy_assignment.deny_postgres_firewall_rules[0]
azurerm_role_assignment.control_reader["cleanup"]
azurerm_role_assignment.control_reader["provision"]
azurerm_role_assignment.source_reader[0]
azurerm_role_assignment.target_cleanup[0]
azurerm_role_assignment.target_provisioner[0]
azurerm_role_definition.control_reader[0]
azurerm_role_definition.marker_reader[0]
azurerm_role_definition.source_reader[0]
azurerm_role_definition.target_cleanup[0]
azurerm_role_definition.target_provisioner[0]
azurerm_storage_account.marker[0]
azurerm_subnet.marker_private_endpoint[0]
azurerm_subnet.private_executor_reserved[0]
azurerm_user_assigned_identity.dispatcher["cleanup"]
azurerm_user_assigned_identity.dispatcher["provision"]
azurerm_user_assigned_identity.job["cleanup"]
azurerm_user_assigned_identity.job["provision"]
azurerm_virtual_network.control[0]
terraform_data.activation_guard[0]
terraform_data.private_executor_topology_blocker[0]
""".strip().splitlines()
)

EXPECTED_CREATE_ADDRESSES: frozenset[str] = frozenset(
    """
azapi_resource.marker_container["restore-cleanups-v3"]
azapi_resource.marker_container["restore-cleanups-v4"]
azapi_resource.marker_container["restore-evidence-v1"]
azapi_resource.marker_container["restore-intents-v3"]
azapi_resource.marker_container["restore-intents-v4"]
azapi_resource.marker_container["restore-submissions-v3"]
azapi_resource.marker_container["restore-submissions-v4"]
azurerm_private_endpoint.marker[0]
azurerm_role_assignment.marker_provision_cleanup[0]
azurerm_role_assignment.marker_read["cleanup_intent"]
azurerm_role_assignment.marker_read["cleanup_submission"]
""".strip().splitlines()
)

EXPECTED_VARIABLES: Mapping[str, Any] = {
    "control_cidr_inventory_revalidated": True,
    "deploy_staging_restore_control_plane": True,
    "enable_marker_create_assignments": False,
    "enable_marker_legal_holds": False,
    "enable_marker_readonly_lock": False,
    "enable_private_executor_network_foundation": False,
    "irreversible_marker_controls_approved": False,
    "lock_marker_immutability_policies": False,
    "marker_create_authorization_proven": False,
    "marker_create_data_actions": [],
    "marker_legal_hold_readback_proven": False,
    "marker_storage_account_name": "cosmaneurarehearsalctl",
    "private_executor_network_plan_approved": False,
    "private_marker_network_posture_proven": False,
    "subscription_id": EXPECTED_SUBSCRIPTION_ID,
    "tags": {
        "data-classification": "non-phi-control-metadata",
        "environment": "staging",
        "managed-by": "opentofu",
        "purpose": "staging-restore-rehearsal-control",
    },
    "target_cidr_inventory_revalidated": True,
    "tenant_id": EXPECTED_TENANT_ID,
}
EXPECTED_BACKEND_CONFIG: Mapping[str, Any] = {
    "resource_group_name": "cosmaneura-tfstate",
    "storage_account_name": "cosmaneuratfstate",
    "container_name": "cosmaneura-tfstate",
    "key": "staging-rehearsal-control-plane.tfstate",
    "subscription_id": EXPECTED_SUBSCRIPTION_ID,
    "environment": "public",
    "use_azuread_auth": True,
    "use_cli": True,
}
EXPECTED_CONTROL_RESOURCE_GROUP_ID = (
    f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
    "cosmaneura-staging-rehearsal-control"
)
EXPECTED_MARKER_STORAGE_ACCOUNT_ID = (
    f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.Storage/"
    "storageAccounts/cosmaneurarehearsalctl"
)
EXPECTED_MARKER_CONTAINER_PARENT_ID = (
    f"{EXPECTED_MARKER_STORAGE_ACCOUNT_ID}/blobServices/default"
)
EXPECTED_MARKER_PRIVATE_DNS_ZONE_ID = (
    f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.Network/"
    "privateDnsZones/privatelink.blob.core.windows.net"
)
EXPECTED_MARKER_SUBNET_ID = (
    f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.Network/"
    "virtualNetworks/cosmaneura-staging-rehearsal-control-vnet/"
    "subnets/marker-private-endpoints"
)
EXPECTED_MARKER_READER_ROLE_ID = (
    f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/providers/"
    "Microsoft.Authorization/roleDefinitions/"
    "3a78ee1a-5f02-5a39-a6d2-634ff2a8a253"
)
EXPECTED_ROLE_ASSIGNMENTS: Mapping[str, tuple[str, str]] = {
    "azurerm_role_assignment.marker_provision_cleanup[0]": (
        "8e5606e8-5cc0-5e0f-ad8f-5ee340a2f7c2",
        "b1a7600b-b8ff-4b8b-ab3b-af483b1619c5",
    ),
    'azurerm_role_assignment.marker_read["cleanup_intent"]': (
        "d795a889-8f20-51c2-bfcc-8e3983cf6f3b",
        "1680af1c-bfa4-4968-baeb-5a175fdd749d",
    ),
    'azurerm_role_assignment.marker_read["cleanup_submission"]': (
        "8caca083-2c21-53ba-8a0f-65d856015c3e",
        "1680af1c-bfa4-4968-baeb-5a175fdd749d",
    ),
}
AZURERM_STATE_PROVIDER = 'provider["registry.opentofu.org/hashicorp/azurerm"]'
TERRAFORM_STATE_PROVIDER = 'provider["terraform.io/builtin/terraform"]'
EXPECTED_STATE_IDENTITIES: Mapping[str, tuple[str, int, str]] = {
    'azurerm_federated_identity_credential.dispatcher["cleanup"]': (
        AZURERM_STATE_PROVIDER,
        0,
        f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/"
        "userAssignedIdentities/cosmaneura-staging-restore-cleanup/"
        "federatedIdentityCredentials/github-staging-restore-cleanup",
    ),
    'azurerm_federated_identity_credential.dispatcher["provision"]': (
        AZURERM_STATE_PROVIDER,
        0,
        f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/"
        "userAssignedIdentities/cosmaneura-staging-restore-provision/"
        "federatedIdentityCredentials/github-staging-restore-provision",
    ),
    "azurerm_policy_definition.deny_postgres_firewall_rules[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/providers/"
        "Microsoft.Authorization/policyDefinitions/"
        "deny-staging-restore-firewall-rules",
    ),
    "azurerm_private_dns_zone.marker_blob[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        EXPECTED_MARKER_PRIVATE_DNS_ZONE_ID,
    ),
    "azurerm_private_dns_zone_virtual_network_link.marker_blob[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        f"{EXPECTED_MARKER_PRIVATE_DNS_ZONE_ID}/virtualNetworkLinks/"
        "cosmaneura-staging-rehearsal-marker-link",
    ),
    "azurerm_resource_group.control[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        EXPECTED_CONTROL_RESOURCE_GROUP_ID,
    ),
    "azurerm_resource_group.target[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
        "cosmaneura-staging-rehearsal",
    ),
    "azurerm_resource_group_policy_assignment.deny_postgres_firewall_rules[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
        "cosmaneura-staging-rehearsal/providers/Microsoft.Authorization/"
        "policyAssignments/deny-staging-restore-firewall-rules",
    ),
    'azurerm_role_assignment.control_reader["cleanup"]': (
        AZURERM_STATE_PROVIDER,
        0,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/providers/"
        "Microsoft.Authorization/roleAssignments/"
        "8e1df710-ef99-5127-926f-43ce2e992553",
    ),
    'azurerm_role_assignment.control_reader["provision"]': (
        AZURERM_STATE_PROVIDER,
        0,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/providers/"
        "Microsoft.Authorization/roleAssignments/"
        "b07454ab-e9a7-5a82-b22b-312fc9c8e53b",
    ),
    "azurerm_role_assignment.source_reader[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
        "cosmaneura-staging/providers/Microsoft.DBforPostgreSQL/flexibleServers/"
        "cosmaneura-staging-postgres/providers/Microsoft.Authorization/"
        "roleAssignments/cb35be04-b595-5d40-98f6-34b95a58ab8d",
    ),
    "azurerm_role_assignment.target_cleanup[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
        "cosmaneura-staging-rehearsal/providers/Microsoft.Authorization/"
        "roleAssignments/2c1af22d-d0ad-5734-b190-e20403c2fc9f",
    ),
    "azurerm_role_assignment.target_provisioner[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
        "cosmaneura-staging-rehearsal/providers/Microsoft.Authorization/"
        "roleAssignments/f6ad6576-89f9-5c59-82dc-0e527c7c841f",
    ),
    "azurerm_role_definition.control_reader[0]": (
        AZURERM_STATE_PROVIDER,
        1,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/providers/"
        "Microsoft.Authorization/roleDefinitions/"
        f"3b95a7e2-f4fa-5ed7-a1ad-7723610455e0|/subscriptions/{EXPECTED_SUBSCRIPTION_ID}",
    ),
    "azurerm_role_definition.marker_reader[0]": (
        AZURERM_STATE_PROVIDER,
        1,
        f"{EXPECTED_MARKER_READER_ROLE_ID}|/subscriptions/{EXPECTED_SUBSCRIPTION_ID}",
    ),
    "azurerm_role_definition.source_reader[0]": (
        AZURERM_STATE_PROVIDER,
        1,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/providers/"
        "Microsoft.Authorization/roleDefinitions/"
        f"4191a01a-8fcc-5710-859b-3e83f3d21fce|/subscriptions/{EXPECTED_SUBSCRIPTION_ID}",
    ),
    "azurerm_role_definition.target_cleanup[0]": (
        AZURERM_STATE_PROVIDER,
        1,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/providers/"
        "Microsoft.Authorization/roleDefinitions/"
        f"642e3081-a3b1-53c6-873f-3badacc97e26|/subscriptions/{EXPECTED_SUBSCRIPTION_ID}",
    ),
    "azurerm_role_definition.target_provisioner[0]": (
        AZURERM_STATE_PROVIDER,
        1,
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}/providers/"
        "Microsoft.Authorization/roleDefinitions/"
        f"0004ec78-d9bb-5a1a-bff9-ebfa89c29e7d|/subscriptions/{EXPECTED_SUBSCRIPTION_ID}",
    ),
    "azurerm_storage_account.marker[0]": (
        AZURERM_STATE_PROVIDER,
        4,
        EXPECTED_MARKER_STORAGE_ACCOUNT_ID,
    ),
    "azurerm_subnet.marker_private_endpoint[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        EXPECTED_MARKER_SUBNET_ID,
    ),
    "azurerm_subnet.private_executor_reserved[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.Network/"
        "virtualNetworks/cosmaneura-staging-rehearsal-control-vnet/"
        "subnets/private-executor-reserved",
    ),
    'azurerm_user_assigned_identity.dispatcher["cleanup"]': (
        AZURERM_STATE_PROVIDER,
        1,
        f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/"
        "userAssignedIdentities/cosmaneura-staging-restore-cleanup",
    ),
    'azurerm_user_assigned_identity.dispatcher["provision"]': (
        AZURERM_STATE_PROVIDER,
        1,
        f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/"
        "userAssignedIdentities/cosmaneura-staging-restore-provision",
    ),
    'azurerm_user_assigned_identity.job["cleanup"]': (
        AZURERM_STATE_PROVIDER,
        1,
        f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/"
        "userAssignedIdentities/cosmaneura-staging-restore-cleanup-job",
    ),
    'azurerm_user_assigned_identity.job["provision"]': (
        AZURERM_STATE_PROVIDER,
        1,
        f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.ManagedIdentity/"
        "userAssignedIdentities/cosmaneura-staging-restore-provision-job",
    ),
    "azurerm_virtual_network.control[0]": (
        AZURERM_STATE_PROVIDER,
        0,
        f"{EXPECTED_CONTROL_RESOURCE_GROUP_ID}/providers/Microsoft.Network/"
        "virtualNetworks/cosmaneura-staging-rehearsal-control-vnet",
    ),
    "terraform_data.activation_guard[0]": (
        TERRAFORM_STATE_PROVIDER,
        0,
        "6c8fec9d-c704-446c-9ebb-b0c254504b94",
    ),
    "terraform_data.private_executor_topology_blocker[0]": (
        TERRAFORM_STATE_PROVIDER,
        0,
        "64c63cb5-6fd8-76da-39ce-4658df4ffc4d",
    ),
}


class RecoveryPlanError(ValueError):
    """Raised when an artifact cannot authorize the recovery plan."""


@dataclass(frozen=True)
class SourceSnapshot:
    commit: str
    terraform_files: Mapping[str, bytes]
    lockfile: bytes
    tree_sha256: str


@dataclass(frozen=True)
class RuntimeSnapshot:
    tofu: Path
    azure_cli: Path
    tofu_sha256: str
    azure_cli_sha256: str
    azure_cli_version_sha256: str
    azure_cli_closure_sha256: str
    runtime_snapshot_sha256: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _canonical_file_set_sha256(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        encoded_name = name.encode("utf-8")
        value = files[name]
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _descriptor_acl_state(descriptor: int, *, label: str) -> tuple[bool, bool]:
    if sys.platform == "darwin":
        try:
            library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            get_acl = library.acl_get_fd_np
            get_entry = library.acl_get_entry
            get_tag = library.acl_get_tag_type
            free_acl = library.acl_free
            get_acl.argtypes = (ctypes.c_int, ctypes.c_int)
            get_acl.restype = ctypes.c_void_p
            get_entry.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
            )
            get_entry.restype = ctypes.c_int
            get_tag.argtypes = (
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_int),
            )
            get_tag.restype = ctypes.c_int
            free_acl.argtypes = (ctypes.c_void_p,)
            free_acl.restype = ctypes.c_int
        except (AttributeError, OSError) as exc:
            raise RecoveryPlanError(f"{label} ACL could not be inspected") from exc
        ctypes.set_errno(0)
        acl = get_acl(descriptor, 0x00000100)
        if not acl:
            if ctypes.get_errno() == errno.ENOENT:
                return False, False
            raise RecoveryPlanError(f"{label} ACL could not be inspected")
        has_allow = False
        try:
            entry = ctypes.c_void_p()
            entry_id = 0
            while True:
                ctypes.set_errno(0)
                result = get_entry(acl, entry_id, ctypes.byref(entry))
                if result == -1 and ctypes.get_errno() == errno.EINVAL:
                    break
                if result != 0 or not entry:
                    raise RecoveryPlanError(f"{label} ACL could not be inspected")
                tag = ctypes.c_int()
                if get_tag(entry, ctypes.byref(tag)) != 0 or tag.value not in {1, 2}:
                    raise RecoveryPlanError(f"{label} ACL could not be inspected")
                has_allow = has_allow or tag.value == 1
                entry_id = -1
        finally:
            if free_acl(acl) != 0:
                raise RecoveryPlanError(f"{label} ACL could not be released")
        return True, has_allow
    listxattr = getattr(os, "listxattr", None)
    if listxattr is None:
        raise RecoveryPlanError(f"{label} ACL could not be inspected")
    try:
        attributes = {os.fsdecode(value) for value in listxattr(descriptor)}
    except (OSError, TypeError) as exc:
        raise RecoveryPlanError(f"{label} ACL could not be inspected") from exc
    has_acl = bool(
        attributes
        & {
            "system.posix_acl_access",
            "system.posix_acl_default",
            "system.nfs4_acl",
        }
    )
    return has_acl, has_acl


def _require_safe_acl(
    descriptor: int, *, label: str, allow_deny_only: bool = False
) -> None:
    has_acl, has_allow = _descriptor_acl_state(descriptor, label=label)
    if has_allow or (has_acl and not allow_deny_only):
        raise RecoveryPlanError(f"{label} ACL grants or inherits external access")


def _reject_symlink_components(
    path: Path, *, label: str, allow_missing_leaf: bool = False
) -> None:
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise RecoveryPlanError(f"{label} path must be absolute and normalized")
    current = Path(path.anchor)
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        current /= part
        is_leaf = index == len(parts) - 1
        try:
            metadata = os.lstat(current)
        except FileNotFoundError as exc:
            if allow_missing_leaf and is_leaf:
                return
            raise RecoveryPlanError(f"{label} path is unavailable") from exc
        except OSError as exc:
            raise RecoveryPlanError(f"{label} path is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RecoveryPlanError(f"{label} path contains a symbolic link")


def _open_protected_directory(path: Path, *, label: str) -> int:
    _reject_symlink_components(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise RecoveryPlanError(f"{label} is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise RecoveryPlanError(f"{label} is not a directory")
    if metadata.st_uid != os.geteuid():
        os.close(descriptor)
        raise RecoveryPlanError(f"{label} is not owned by the current operator")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o077 or mode & 0o700 != 0o700:
        os.close(descriptor)
        raise RecoveryPlanError(f"{label} must have mode 0700")
    try:
        _require_safe_acl(descriptor, label=label)
    except RecoveryPlanError:
        os.close(descriptor)
        raise
    return descriptor


def _require_protected_directory(path: Path, *, label: str) -> None:
    os.close(_open_protected_directory(path, label=label))


def _create_protected_directory(path: Path, *, label: str) -> None:
    _reject_symlink_components(path, label=label, allow_missing_leaf=True)
    parent = _open_protected_directory(path.parent, label=f"{label} parent")
    previous_umask: int | None = None
    try:
        previous_umask = os.umask(0o077)
        os.mkdir(path.name, 0o700, dir_fd=parent)
    except OSError as exc:
        raise RecoveryPlanError(f"{label} must not already exist") from exc
    finally:
        if previous_umask is not None:
            os.umask(previous_umask)
        os.close(parent)
    _require_protected_directory(path, label=label)


def _executable_sha256(path: Path, *, label: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not before.st_mode & 0o111:
            raise RecoveryPlanError(f"{label} is not an executable regular file")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RecoveryPlanError(f"{label} changed while it was hashed")
        return digest.hexdigest()
    except OSError as exc:
        raise RecoveryPlanError(f"{label} could not be hashed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _trusted_executable_source(path: Path, *, label: str, expected_sha256: str) -> Path:
    _reject_symlink_components(path, label=label)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise RecoveryPlanError(f"{label} is unavailable") from exc
        if metadata.st_uid not in {0, os.geteuid()}:
            raise RecoveryPlanError(f"{label} has untrusted ownership")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise RecoveryPlanError(f"{label} has a writable path component")
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise RecoveryPlanError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not metadata.st_mode & 0o111
        or not 0 < metadata.st_size <= MAXIMUM_EXECUTABLE_BYTES
        or _executable_sha256(path, label=label) != expected_sha256
    ):
        raise RecoveryPlanError(f"{label} does not match the pinned executable")
    return path


def _validate_runtime_snapshot(path: Path, *, label: str, expected_sha256: str) -> None:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise RecoveryPlanError(f"{label} snapshot is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o500
        or not 0 < metadata.st_size <= MAXIMUM_EXECUTABLE_BYTES
        or _executable_sha256(path, label=f"{label} snapshot") != expected_sha256
    ):
        raise RecoveryPlanError(f"{label} snapshot is not exact")


def _snapshot_executable(
    source: Path,
    destination: Path,
    *,
    label: str,
    expected_sha256: str,
) -> Path:
    _trusted_executable_source(
        source, label=f"pinned {label}", expected_sha256=expected_sha256
    )
    if destination.exists():
        _validate_runtime_snapshot(
            destination, label=label, expected_sha256=expected_sha256
        )
        return destination

    parent = _open_protected_directory(
        destination.parent, label="trusted runtime directory"
    )
    source_flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    created = False
    try:
        source_descriptor = os.open(source, source_flags)
        before = os.fstat(source_descriptor)
        destination_descriptor = os.open(
            destination.name, destination_flags, 0o500, dir_fd=parent
        )
        created = True
        digest = hashlib.sha256()
        copied = 0
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            copied += len(chunk)
            offset = 0
            while offset < len(chunk):
                offset += os.write(destination_descriptor, chunk[offset:])
        after = os.fstat(source_descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if (
            copied != before.st_size
            or identity_before != identity_after
            or digest.hexdigest() != expected_sha256
        ):
            raise RecoveryPlanError(f"pinned {label} changed during snapshot")
        os.fchmod(destination_descriptor, 0o500)
        os.fsync(destination_descriptor)
    except (OSError, RecoveryPlanError) as exc:
        if created:
            try:
                os.unlink(destination.name, dir_fd=parent)
            except OSError:
                pass
        if isinstance(exc, RecoveryPlanError):
            raise
        raise RecoveryPlanError(f"{label} snapshot could not be created") from exc
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        os.close(parent)
    _validate_runtime_snapshot(
        destination, label=label, expected_sha256=expected_sha256
    )
    return destination


def _runtime_file_record(
    path: Path, *, label: str, capture: bytearray | None = None
) -> tuple[Any, ...]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RecoveryPlanError(f"{label} is not a regular file")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            if capture is not None:
                capture.extend(chunk)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity:
            raise RecoveryPlanError(f"{label} changed while it was hashed")
        return (
            "file",
            bool(stat.S_IMODE(before.st_mode) & 0o111),
            before.st_size,
            digest.hexdigest(),
        )
    except OSError as exc:
        raise RecoveryPlanError(f"{label} could not be hashed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


# fmt: off
def _azure_directory_identity(value: os.stat_result) -> tuple[int, ...]: return value.st_dev, value.st_ino, value.st_uid, stat.S_IMODE(value.st_mode)
def _azure_file_identity(value: os.stat_result) -> tuple[int, ...]: return value.st_dev, value.st_ino, value.st_uid, value.st_nlink, stat.S_IMODE(value.st_mode), value.st_size, value.st_mtime_ns, value.st_ctime_ns
def _validated_azure_config(home: Path) -> tuple[Path, str]:
    if not home.is_absolute() or home != Path(os.path.abspath(home)): raise RecoveryPlanError("Azure CLI home path is invalid")  # noqa: E701
    directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0); descriptors: list[int] = []  # noqa: E702
    try:
        home_descriptor = os.open(home, directory_flags); descriptors.append(home_descriptor); home_metadata = os.fstat(home_descriptor)  # noqa: E702
        config_descriptor = os.open(".azure", directory_flags, dir_fd=home_descriptor); descriptors.append(config_descriptor); config_metadata = os.fstat(config_descriptor)  # noqa: E702
        if not stat.S_ISDIR(home_metadata.st_mode) or home_metadata.st_uid != os.geteuid() or stat.S_IMODE(home_metadata.st_mode) & 0o022 or stat.S_IMODE(home_metadata.st_mode) & 0o500 != 0o500 or not stat.S_ISDIR(config_metadata.st_mode) or config_metadata.st_uid != os.geteuid() or stat.S_IMODE(config_metadata.st_mode) != 0o700: raise RecoveryPlanError("Azure CLI configuration path is unsafe")  # noqa: E701
        _require_safe_acl(home_descriptor, label="Azure CLI home", allow_deny_only=True); _require_safe_acl(config_descriptor, label="Azure CLI configuration directory")  # noqa: E702
        bindings: list[tuple[Any, ...]] = [("home", *_azure_directory_identity(home_metadata)), ("config", *_azure_directory_identity(config_metadata))]
        for name in AZURE_CONFIG_FILES:
            descriptor = os.open(name, file_flags, dir_fd=config_descriptor); descriptors.append(descriptor); before = os.fstat(descriptor); mode = stat.S_IMODE(before.st_mode)  # noqa: E702
            if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1 or mode not in {0o400, 0o600} or not 0 < before.st_size <= MAXIMUM_AZURE_CONFIG_FILE_BYTES: raise RecoveryPlanError("Azure CLI configuration is not exact")  # noqa: E701
            _require_safe_acl(descriptor, label="Azure CLI configuration file")
            with os.fdopen(os.dup(descriptor), "rb") as stream: value = stream.read(MAXIMUM_AZURE_CONFIG_FILE_BYTES + 1)  # noqa: E701
            after = os.fstat(descriptor); linked = os.stat(name, dir_fd=config_descriptor, follow_symlinks=False)  # noqa: E702
            _require_safe_acl(descriptor, label="Azure CLI configuration file")
            if len(value) != before.st_size or _azure_file_identity(before) != _azure_file_identity(after) or _azure_file_identity(before) != _azure_file_identity(linked): raise RecoveryPlanError("Azure CLI configuration changed while captured")  # noqa: E701
            digest = _sha256_bytes(value)
            if name in EXPECTED_AZURE_CONFIG_SHA256S and digest != EXPECTED_AZURE_CONFIG_SHA256S[name]: raise RecoveryPlanError("Azure CLI configuration is not exact")  # noqa: E701
            bindings.append((name, *_azure_file_identity(before), digest))
        _require_safe_acl(home_descriptor, label="Azure CLI home", allow_deny_only=True); _require_safe_acl(config_descriptor, label="Azure CLI configuration directory")  # noqa: E702
        if _azure_directory_identity(home_metadata) != _azure_directory_identity(os.stat(home, follow_symlinks=False)) or _azure_directory_identity(config_metadata) != _azure_directory_identity(os.stat(".azure", dir_fd=home_descriptor, follow_symlinks=False)): raise RecoveryPlanError("Azure CLI configuration path changed")  # noqa: E701
        return home / ".azure", _sha256_bytes(_canonical_json_bytes(bindings))
    except OSError as exc:
        raise RecoveryPlanError("Azure CLI configuration is unavailable") from exc
    finally:
        for descriptor in reversed(descriptors): os.close(descriptor)  # noqa: E701
# fmt: on


def _open_azure_config_lock(runtime_directory: Path) -> int:
    parent = _open_protected_directory(
        runtime_directory, label="trusted runtime directory"
    )
    descriptor: int | None = None
    try:
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(AZURE_CONFIG_LOCK_NAME, flags, dir_fd=parent)
        except FileNotFoundError:
            previous_umask: int | None = None
            try:
                previous_umask = os.umask(0o077)
                descriptor = os.open(
                    AZURE_CONFIG_LOCK_NAME,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent,
                )
            except FileExistsError:
                descriptor = os.open(AZURE_CONFIG_LOCK_NAME, flags, dir_fd=parent)
            finally:
                if previous_umask is not None:
                    os.umask(previous_umask)
        metadata = os.fstat(descriptor)
        linked = os.stat(AZURE_CONFIG_LOCK_NAME, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
            or _azure_file_identity(metadata) != _azure_file_identity(linked)
        ):
            raise RecoveryPlanError("Azure configuration lock is unsafe")
        _require_safe_acl(descriptor, label="Azure configuration lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryPlanError("Azure configuration runtime is active") from exc
        current = os.fstat(descriptor)
        linked = os.stat(AZURE_CONFIG_LOCK_NAME, dir_fd=parent, follow_symlinks=False)
        if _azure_file_identity(metadata) != _azure_file_identity(
            current
        ) or _azure_file_identity(metadata) != _azure_file_identity(linked):
            raise RecoveryPlanError("Azure configuration lock changed")  # fmt: skip
        return descriptor
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise RecoveryPlanError("Azure configuration lock is unavailable") from exc
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(parent)


def _remove_stale_azure_config_snapshots(runtime_directory: Path) -> None:
    lock = _open_azure_config_lock(runtime_directory)
    parent: int | None = None
    try:
        parent = _open_protected_directory(
            runtime_directory, label="trusted runtime directory"
        )
        if not shutil.rmtree.avoids_symlink_attacks:
            raise RecoveryPlanError("secure Azure configuration cleanup is unavailable")
        for name in os.listdir(parent):
            if re.fullmatch(r"\.azure-config-[A-Za-z0-9_-]{6,96}", name) is None:
                continue
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
            descriptor = os.open(name, flags, dir_fd=parent)
            try:
                metadata = os.fstat(descriptor)
                linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                ):
                    raise RecoveryPlanError(
                        "stale Azure configuration snapshot is unsafe"
                    )
                identity = (metadata.st_dev, metadata.st_ino, metadata.st_uid)
                metadata_safe = stat.S_IMODE(metadata.st_mode) in {0o500, 0o700}
                metadata_safe = metadata_safe and identity == (
                    linked.st_dev,
                    linked.st_ino,
                    linked.st_uid,
                )
                os.fchmod(descriptor, 0o700)
                scrubbed = True
                for entry in os.listdir(descriptor):
                    entry_metadata = os.stat(
                        entry, dir_fd=descriptor, follow_symlinks=False
                    )
                    if entry_metadata.st_uid != os.geteuid() or not (
                        stat.S_ISDIR(entry_metadata.st_mode)
                        or stat.S_ISREG(entry_metadata.st_mode)
                        or stat.S_ISLNK(entry_metadata.st_mode)
                    ):
                        scrubbed = False
                        continue
                    if stat.S_ISDIR(entry_metadata.st_mode):
                        shutil.rmtree(entry, dir_fd=descriptor)
                    else:
                        os.unlink(entry, dir_fd=descriptor)
                if os.listdir(descriptor):
                    scrubbed = False
                try:
                    _require_safe_acl(
                        descriptor, label="stale Azure configuration snapshot"
                    )
                except RecoveryPlanError:
                    metadata_safe = False
                current = os.fstat(descriptor)
                linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
                identity_safe = identity == (
                    current.st_dev,
                    current.st_ino,
                    current.st_uid,
                ) and identity == (linked.st_dev, linked.st_ino, linked.st_uid)
                if scrubbed and identity_safe:
                    os.rmdir(name, dir_fd=parent)
                    try:
                        os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise RecoveryPlanError(
                            "stale Azure configuration snapshot was not removed"
                        )
                if not scrubbed or not metadata_safe:
                    raise RecoveryPlanError(
                        "stale Azure configuration snapshot is unsafe"
                    )
                if not identity_safe:
                    raise RecoveryPlanError(
                        "stale Azure configuration snapshot changed"
                    )
            finally:
                os.close(descriptor)
    except OSError as exc:
        raise RecoveryPlanError(
            "stale Azure configuration snapshot could not be removed"
        ) from exc
    finally:
        if parent is not None:
            os.close(parent)
        os.close(lock)


def _prepare_and_verify_runtime_home(
    runtime_directory: Path, *, create_missing: bool
) -> None:
    home = runtime_directory / "azure-home"
    terraform_home = home / ".terraform.d"
    for directory, label in (
        (home, "trusted runtime home"),
        (terraform_home, "trusted OpenTofu home"),
    ):
        if directory.exists():
            _require_protected_directory(directory, label=label)
        elif create_missing:
            _create_protected_directory(directory, label=label)
        else:
            raise RecoveryPlanError(f"{label} is unavailable")
    try:
        home_names = {entry.name for entry in home.iterdir()}
        terraform_names = {entry.name for entry in terraform_home.iterdir()}
    except OSError as exc:
        raise RecoveryPlanError("trusted runtime home is unavailable") from exc
    if home_names != {".terraform.d"} or terraform_names:
        raise RecoveryPlanError("trusted runtime home is not exact")


def _azure_cli_closure_entries(
    homebrew_root: Path, *, snapshot: bool
) -> Mapping[str, tuple[Any, ...]]:
    if not homebrew_root.is_absolute() or homebrew_root != Path(
        os.path.abspath(homebrew_root)
    ):
        raise RecoveryPlanError("Azure CLI closure root is invalid")
    entries: dict[str, tuple[Any, ...]] = {}
    total_bytes = 0

    def add(path: Path) -> None:
        nonlocal total_bytes
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise RecoveryPlanError("Azure CLI closure entry is unavailable") from exc
        if metadata.st_uid not in {0, os.geteuid()}:
            raise RecoveryPlanError("Azure CLI closure ownership is not trusted")
        mode = stat.S_IMODE(metadata.st_mode)
        relative = path.relative_to(homebrew_root).as_posix()
        if relative in entries:
            raise RecoveryPlanError("Azure CLI closure contains a duplicate entry")
        if stat.S_ISLNK(metadata.st_mode):
            try:
                target = os.readlink(path)
            except OSError as exc:
                raise RecoveryPlanError(
                    "Azure CLI closure link is unavailable"
                ) from exc
            if Path(target).is_absolute():
                raise RecoveryPlanError("Azure CLI closure contains an absolute link")
            lexical_target = Path(os.path.abspath(path.parent / target))
            if not lexical_target.is_relative_to(homebrew_root):
                raise RecoveryPlanError("Azure CLI closure link escapes its root")
            try:
                resolved_target = path.resolve(strict=True)
            except OSError as exc:
                raise RecoveryPlanError("Azure CLI closure link is invalid") from exc
            if not resolved_target.is_relative_to(homebrew_root):
                raise RecoveryPlanError("Azure CLI closure link escapes its root")
            entries[relative] = ("link", target)
        elif stat.S_ISDIR(metadata.st_mode):
            if (snapshot and mode & 0o222) or (not snapshot and mode & 0o022):
                raise RecoveryPlanError("Azure CLI closure permissions are not trusted")
            entries[relative] = ("directory",)
        elif stat.S_ISREG(metadata.st_mode):
            if (snapshot and mode & 0o222) or (not snapshot and mode & 0o022):
                raise RecoveryPlanError("Azure CLI closure permissions are not trusted")
            record = _runtime_file_record(path, label="Azure CLI closure file")
            total_bytes += int(record[2])
            if total_bytes > MAXIMUM_RUNTIME_CLOSURE_BYTES:
                raise RecoveryPlanError("Azure CLI closure is too large")
            entries[relative] = record
        else:
            raise RecoveryPlanError("Azure CLI closure contains an unsafe file type")
        if len(entries) > MAXIMUM_RUNTIME_CLOSURE_ENTRIES:
            raise RecoveryPlanError("Azure CLI closure contains too many entries")

    for formula, version in sorted(EXPECTED_AZURE_CLI_FORMULAE.items()):
        formula_root = homebrew_root / "Cellar" / formula / version
        _reject_symlink_components(formula_root, label="Azure CLI formula")
        add(formula_root)
        for directory, names, files in os.walk(formula_root, followlinks=False):
            names.sort()
            files.sort()
            for name in (*names, *files):
                add(Path(directory) / name)
        opt_link = homebrew_root / "opt" / formula
        try:
            target = os.readlink(opt_link)
        except OSError as exc:
            raise RecoveryPlanError("Azure CLI formula link is unavailable") from exc
        if target != f"../Cellar/{formula}/{version}":
            raise RecoveryPlanError("Azure CLI formula link target is not pinned")
        add(opt_link)
    return entries


def _runtime_entries_sha256(entries: Mapping[str, tuple[Any, ...]]) -> str:
    return _sha256_bytes(
        _canonical_json_bytes([[name, *entries[name]] for name in sorted(entries)])
    )


def _azure_cli_environment(runtime_directory: Path) -> Mapping[str, str]:
    homebrew = runtime_directory / "homebrew"
    extensions = runtime_directory / "azure-extensions"
    azure_version = EXPECTED_AZURE_CLI_FORMULAE["azure-cli"]
    python_version = EXPECTED_AZURE_CLI_FORMULAE["python@3.14"]
    library_directories = [
        homebrew
        / "Cellar"
        / "python@3.14"
        / python_version
        / "Frameworks"
        / "Python.framework"
        / "Versions"
        / "3.14",
        *(
            homebrew / "opt" / formula / "lib"
            for formula in (
                "libsodium",
                "libyaml",
                "lz4",
                "mpdecimal",
                "openssl@3",
                "readline",
                "sqlite",
                "xz",
                "zstd",
            )
        ),
    ]
    site_packages = (
        homebrew
        / "Cellar"
        / "azure-cli"
        / azure_version
        / "libexec"
        / "lib"
        / "python3.14"
        / "site-packages"
    )
    python_home = (
        homebrew
        / "Cellar"
        / "python@3.14"
        / python_version
        / "Frameworks"
        / "Python.framework"
        / "Versions"
        / "3.14"
    )
    certificate = site_packages / "certifi" / "cacert.pem"
    openssl_modules = homebrew / "opt" / "openssl@3" / "lib" / "ossl-modules"
    return {
        "AZ_INSTALLER": "HOMEBREW",
        "AZURE_CORE_COLLECT_TELEMETRY": "false",
        "AZURE_CORE_DISABLE_CONFIRM_PROMPT": "true",
        "AZURE_CORE_ONLY_SHOW_ERRORS": "true",
        "AZURE_EXTENSION_DEV_SOURCES": "",
        "AZURE_EXTENSION_DIR": str(extensions),
        "AZURE_EXTENSION_SYS_DIR": str(extensions),
        "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no",
        "AZURE_LOGGING_ENABLE_LOG_FILE": "false",
        "AZURE_CORE_USE_MSAL_HTTP_CACHE": "false",
        "DYLD_LIBRARY_PATH": ":".join(map(str, library_directories)),
        "HOME": str(runtime_directory / "azure-home"),
        "OPENSSL_CONF": os.devnull,
        "OPENSSL_MODULES": str(openssl_modules),
        "PYTHONHASHSEED": "0",
        "PYTHONHOME": str(python_home),
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(site_packages),
        "PYTHONSAFEPATH": "1",
        "REQUESTS_CA_BUNDLE": str(certificate),
        "SSL_CERT_FILE": str(certificate),
    }


# fmt: off
def _azure_cli_guard_source(runtime_directory: Path) -> str:
    source = r"""import ctypes,errno,fcntl,hashlib,json,os,runpy,secrets,shutil,signal,stat,sys
EXPECTED=__EXPECTED__;NAMES=__NAMES__;MAXIMUM=__MAXIMUM__;PARENT=__PARENT__;BINDING_ENV=__BINDING_ENV__;LOCK=__LOCK__;DIRECTORY_FLAGS=os.O_RDONLY|getattr(os,"O_CLOEXEC",0)|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_DIRECTORY",0);FILE_FLAGS=os.O_RDONLY|getattr(os,"O_CLOEXEC",0)|getattr(os,"O_NOFOLLOW",0);LOCK_FLAGS=os.O_RDWR|getattr(os,"O_CLOEXEC",0)|getattr(os,"O_NOFOLLOW",0);TERMINATION_SIGNALS=tuple(value for value in (getattr(signal,"SIGHUP",None),signal.SIGINT,signal.SIGTERM,getattr(signal,"SIGQUIT",None)) if value is not None)
def directory_identity(value):return value.st_dev,value.st_ino,value.st_uid,stat.S_IMODE(value.st_mode)
def file_identity(value):return value.st_dev,value.st_ino,value.st_uid,value.st_nlink,stat.S_IMODE(value.st_mode),value.st_size,value.st_mtime_ns,value.st_ctime_ns
def acl_state(fd):
    if sys.platform=="darwin":
        library=ctypes.CDLL("/usr/lib/libSystem.B.dylib",use_errno=True);get_acl=library.acl_get_fd_np;get_entry=library.acl_get_entry;get_tag=library.acl_get_tag_type;free_acl=library.acl_free
        get_acl.argtypes=(ctypes.c_int,ctypes.c_int);get_acl.restype=ctypes.c_void_p;get_entry.argtypes=(ctypes.c_void_p,ctypes.c_int,ctypes.POINTER(ctypes.c_void_p));get_entry.restype=ctypes.c_int;get_tag.argtypes=(ctypes.c_void_p,ctypes.POINTER(ctypes.c_int));get_tag.restype=ctypes.c_int;free_acl.argtypes=(ctypes.c_void_p,);free_acl.restype=ctypes.c_int
        ctypes.set_errno(0);acl=get_acl(fd,0x00000100)
        if not acl:
            if ctypes.get_errno()==errno.ENOENT:return False,False
            raise OSError
        has_allow=False
        try:
            entry=ctypes.c_void_p();entry_id=0
            while True:
                ctypes.set_errno(0);result=get_entry(acl,entry_id,ctypes.byref(entry))
                if result==-1 and ctypes.get_errno()==errno.EINVAL:break
                if result!=0 or not entry:raise OSError
                tag=ctypes.c_int()
                if get_tag(entry,ctypes.byref(tag))!=0 or tag.value not in (1,2):raise OSError
                has_allow=has_allow or tag.value==1;entry_id=-1
        finally:
            if free_acl(acl)!=0:raise OSError
        return True,has_allow
    listxattr=getattr(os,"listxattr",None)
    if listxattr is None:raise OSError
    attributes={os.fsdecode(value) for value in listxattr(fd)};has_acl=bool(attributes&{"system.posix_acl_access","system.posix_acl_default","system.nfs4_acl"});return has_acl,has_acl
def safe_acl(fd,deny_only=False):
    has_acl,has_allow=acl_state(fd)
    if has_allow or (has_acl and not deny_only):raise ValueError
def capture(source):
    if not os.path.isabs(source) or os.path.normpath(source)!=source or os.path.basename(source)!=".azure":raise ValueError
    home=os.path.dirname(source);descriptors=[]
    try:
        home_fd=os.open(home,DIRECTORY_FLAGS);descriptors.append(home_fd);home_metadata=os.fstat(home_fd)
        config_fd=os.open(".azure",DIRECTORY_FLAGS,dir_fd=home_fd);descriptors.append(config_fd);config_metadata=os.fstat(config_fd)
        if not stat.S_ISDIR(home_metadata.st_mode) or home_metadata.st_uid!=os.geteuid() or stat.S_IMODE(home_metadata.st_mode)&0o022 or stat.S_IMODE(home_metadata.st_mode)&0o500!=0o500 or not stat.S_ISDIR(config_metadata.st_mode) or config_metadata.st_uid!=os.geteuid() or stat.S_IMODE(config_metadata.st_mode)!=0o700:raise ValueError
        safe_acl(home_fd,True);safe_acl(config_fd);bindings=[("home",*directory_identity(home_metadata)),("config",*directory_identity(config_metadata))];captures={}
        for name in NAMES:
            fd=os.open(name,FILE_FLAGS,dir_fd=config_fd);descriptors.append(fd);before=os.fstat(fd);mode=stat.S_IMODE(before.st_mode)
            if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid() or before.st_nlink!=1 or mode not in (0o400,0o600) or not 0<before.st_size<=MAXIMUM:raise ValueError
            safe_acl(fd)
            with os.fdopen(os.dup(fd),"rb") as stream:value=stream.read(MAXIMUM+1)
            after=os.fstat(fd);linked=os.stat(name,dir_fd=config_fd,follow_symlinks=False)
            safe_acl(fd)
            if len(value)!=before.st_size or file_identity(before)!=file_identity(after) or file_identity(before)!=file_identity(linked):raise ValueError
            digest=hashlib.sha256(value).hexdigest()
            if name in EXPECTED and digest!=EXPECTED[name]:raise ValueError
            captures[name]=value;bindings.append((name,*file_identity(before),digest))
        safe_acl(home_fd,True);safe_acl(config_fd)
        if directory_identity(home_metadata)!=directory_identity(os.stat(home,follow_symlinks=False)) or directory_identity(config_metadata)!=directory_identity(os.stat(".azure",dir_fd=home_fd,follow_symlinks=False)):raise ValueError
        binding=hashlib.sha256(json.dumps(bindings,sort_keys=True,separators=(",",":")).encode()).hexdigest();return binding,captures
    finally:[os.close(fd) for fd in reversed(descriptors)]
snapshot=snapshot_name=snapshot_identity=None;snapshot_fd=parent_fd=lock_fd=None;snapshot_created=False
def scrub(fd):
    ok=True
    try:
        os.fchmod(fd,0o700)
        for name in os.listdir(fd):
            metadata=os.stat(name,dir_fd=fd,follow_symlinks=False)
            if metadata.st_uid!=os.geteuid() or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):ok=False;continue
            if stat.S_ISDIR(metadata.st_mode):shutil.rmtree(name,dir_fd=fd)
            else:os.unlink(name,dir_fd=fd)
        return ok and not os.listdir(fd)
    except OSError:return False
def remove_snapshot(name,fd,identity):
    if not scrub(fd):return False
    try:
        current=os.fstat(fd);linked=os.stat(name,dir_fd=parent_fd,follow_symlinks=False)
        if identity!=(current.st_dev,current.st_ino,current.st_uid) or identity!=(linked.st_dev,linked.st_ino,linked.st_uid):return False
        os.rmdir(name,dir_fd=parent_fd)
        try:os.stat(name,dir_fd=parent_fd,follow_symlinks=False);return False
        except FileNotFoundError:return True
    except OSError:return False
def cleanup():
    global snapshot_fd,parent_fd,lock_fd
    ok=True;previous_mask=None
    try:previous_mask=signal.pthread_sigmask(signal.SIG_BLOCK,TERMINATION_SIGNALS)
    except (AttributeError,OSError):ok=False
    if snapshot_created and snapshot_name is not None and parent_fd is not None:
        created_fd=snapshot_fd;created_fd_was_established=created_fd is not None
        try:
            if created_fd is None:created_fd=os.open(snapshot_name,DIRECTORY_FLAGS,dir_fd=parent_fd)
            created_metadata=os.fstat(created_fd);created_linked=os.stat(snapshot_name,dir_fd=parent_fd,follow_symlinks=False);created_identity=(created_metadata.st_dev,created_metadata.st_ino,created_metadata.st_uid)
            if created_fd_was_established:
                metadata_safe=stat.S_ISDIR(created_metadata.st_mode) and created_metadata.st_uid==os.geteuid() and stat.S_IMODE(created_metadata.st_mode)==0o700
                if snapshot_identity is None or not remove_snapshot(snapshot_name,created_fd,snapshot_identity):raise ValueError
                try:safe_acl(created_fd)
                except (OSError,ValueError):metadata_safe=False
                if not metadata_safe:ok=False
            else:
                if not stat.S_ISDIR(created_metadata.st_mode) or created_metadata.st_uid!=os.geteuid() or stat.S_IMODE(created_metadata.st_mode)!=0o700 or created_identity!=(created_linked.st_dev,created_linked.st_ino,created_linked.st_uid) or os.listdir(created_fd) or snapshot_identity is not None:raise ValueError
                safe_acl(created_fd)
                if not remove_snapshot(snapshot_name,created_fd,created_identity):raise ValueError
        except (OSError,ValueError):ok=False
        finally:
            if created_fd is not None:os.close(created_fd)
            snapshot_fd=None
    if parent_fd is not None:
        try:os.close(parent_fd)
        except OSError:ok=False
        parent_fd=None
    if lock_fd is not None:
        try:os.close(lock_fd)
        except OSError:ok=False
        lock_fd=None
    if previous_mask is not None and ok:signal.pthread_sigmask(signal.SIG_SETMASK,previous_mask)
    return ok
def cleanup_until_complete():
    interrupted=None
    while True:
        try:return cleanup(),interrupted
        except SystemExit as error:
            if interrupted is None:interrupted=error
def valid_snapshot_name(name):
    suffix=name.removeprefix(".azure-config-");return name.startswith(".azure-config-") and 6<=len(suffix)<=96 and all(value.isascii() and (value.isalnum() or value in "_-") for value in suffix)
def terminate(signum,_frame):raise SystemExit(128+signum)
try:
    try:
        if not shutil.rmtree.avoids_symlink_attacks or not hasattr(signal,"pthread_sigmask"):raise ValueError
        previous_mask=signal.pthread_sigmask(signal.SIG_BLOCK,set())
        if any(value in previous_mask for value in TERMINATION_SIGNALS):raise ValueError
        for value in TERMINATION_SIGNALS:signal.signal(value,terminate)
        expected_binding=os.environ.pop(BINDING_ENV)
        if len(expected_binding)!=64 or any(value not in "0123456789abcdef" for value in expected_binding):raise ValueError
        binding,captures=capture(os.environ["AZURE_CONFIG_DIR"])
        if binding!=expected_binding:raise ValueError
        parent_fd=os.open(PARENT,DIRECTORY_FLAGS);parent_metadata=os.fstat(parent_fd);safe_acl(parent_fd)
        if parent_metadata.st_uid!=os.geteuid() or stat.S_IMODE(parent_metadata.st_mode)!=0o700:raise ValueError
        lock_fd=os.open(LOCK,LOCK_FLAGS,dir_fd=parent_fd);lock_metadata=os.fstat(lock_fd);lock_linked=os.stat(LOCK,dir_fd=parent_fd,follow_symlinks=False)
        if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_uid!=os.geteuid() or lock_metadata.st_nlink!=1 or stat.S_IMODE(lock_metadata.st_mode)!=0o600 or lock_metadata.st_size!=0 or file_identity(lock_metadata)!=file_identity(lock_linked):raise ValueError
        safe_acl(lock_fd);fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB);lock_current=os.fstat(lock_fd);lock_linked=os.stat(LOCK,dir_fd=parent_fd,follow_symlinks=False)
        if file_identity(lock_metadata)!=file_identity(lock_current) or file_identity(lock_metadata)!=file_identity(lock_linked):raise ValueError
        stale_mask=signal.pthread_sigmask(signal.SIG_BLOCK,TERMINATION_SIGNALS)
        try:
            for stale_name in os.listdir(parent_fd):
                if not valid_snapshot_name(stale_name):continue
                stale_fd=os.open(stale_name,DIRECTORY_FLAGS,dir_fd=parent_fd)
                try:
                    stale_metadata=os.fstat(stale_fd);stale_linked=os.stat(stale_name,dir_fd=parent_fd,follow_symlinks=False)
                    if not stat.S_ISDIR(stale_metadata.st_mode) or stale_metadata.st_uid!=os.geteuid():raise ValueError
                    stale_metadata_safe=stat.S_IMODE(stale_metadata.st_mode) in (0o500,0o700) and directory_identity(stale_metadata)==directory_identity(stale_linked);stale_identity=(stale_metadata.st_dev,stale_metadata.st_ino,stale_metadata.st_uid)
                    if not remove_snapshot(stale_name,stale_fd,stale_identity):raise ValueError
                    try:safe_acl(stale_fd)
                    except (OSError,ValueError):stale_metadata_safe=False
                    if not stale_metadata_safe:raise ValueError
                finally:os.close(stale_fd)
        finally:signal.pthread_sigmask(signal.SIG_SETMASK,stale_mask)
        creation_mask=signal.pthread_sigmask(signal.SIG_BLOCK,TERMINATION_SIGNALS);previous_umask=os.umask(0o077)
        try:
            for _ in range(16):
                snapshot_name=".azure-config-"+secrets.token_hex(16)
                try:os.mkdir(snapshot_name,0o700,dir_fd=parent_fd);snapshot_created=True;break
                except FileExistsError:continue
            else:raise ValueError
            snapshot=os.path.join(PARENT,snapshot_name);snapshot_fd=os.open(snapshot_name,DIRECTORY_FLAGS,dir_fd=parent_fd);metadata=os.fstat(snapshot_fd);snapshot_identity=(metadata.st_dev,metadata.st_ino,metadata.st_uid);linked=os.stat(snapshot_name,dir_fd=parent_fd,follow_symlinks=False)
            if metadata.st_uid!=os.geteuid() or stat.S_IMODE(metadata.st_mode)!=0o700 or directory_identity(metadata)!=directory_identity(linked):raise ValueError
            safe_acl(snapshot_fd)
        finally:os.umask(previous_umask);signal.pthread_sigmask(signal.SIG_SETMASK,creation_mask)
        for name,value in captures.items():
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_CLOEXEC",0)|getattr(os,"O_NOFOLLOW",0),0o600,dir_fd=snapshot_fd)
            try:
                safe_acl(fd);offset=0
                while offset<len(value):offset+=os.write(fd,value[offset:])
                os.fchmod(fd,0o600);os.fsync(fd);written=os.fstat(fd)
                if stat.S_IMODE(written.st_mode)!=0o600 or written.st_uid!=os.geteuid() or written.st_nlink!=1:raise ValueError
                safe_acl(fd)
            finally:os.close(fd)
        os.fsync(snapshot_fd);os.environ["AZURE_CONFIG_DIR"]=snapshot;sys.argv=["az",*sys.argv[1:]]
    except BaseException:
        print("azure_cli_guard_refused",file=sys.stderr);raise SystemExit(2)
    runpy.run_module("azure.cli",run_name="__main__")
finally:
    cleanup_ok,cleanup_interrupted=cleanup_until_complete()
    if not cleanup_ok:print("azure_cli_guard_refused",file=sys.stderr);raise SystemExit(2)
    if cleanup_interrupted is not None:raise cleanup_interrupted
"""
    return source.replace("__EXPECTED__", repr(dict(EXPECTED_AZURE_CONFIG_SHA256S))).replace("__NAMES__", repr(AZURE_CONFIG_FILES)).replace("__MAXIMUM__", str(MAXIMUM_AZURE_CONFIG_FILE_BYTES)).replace("__PARENT__", repr(str(runtime_directory))).replace("__BINDING_ENV__", repr(AZURE_CONFIG_BINDING_ENV)).replace("__LOCK__", repr(AZURE_CONFIG_LOCK_NAME))
def _azure_cli_wrapper(runtime_directory: Path) -> bytes:
    homebrew = runtime_directory / "homebrew"
    python_version = EXPECTED_AZURE_CLI_FORMULAE["python@3.14"]
    python = homebrew / "Cellar" / "python@3.14" / python_version / "Frameworks" / "Python.framework" / "Versions" / "3.14" / "bin" / "python3.14"
    lines = ["#!/bin/sh", "set -eu"]
    lines.extend(f"export {name}={shlex.quote(value)}" for name, value in _azure_cli_environment(runtime_directory).items())
    guard = shlex.quote(_azure_cli_guard_source(runtime_directory)); lines.append(f'exec {shlex.quote(str(python))} -sP -c {guard} "$@"')  # noqa: E702
    return ("\n".join(lines) + "\n").encode()
# fmt: on


def _expected_azure_cli_snapshot_entries(
    source_entries: Mapping[str, tuple[Any, ...]],
    runtime_directory: Path,
    source_pyvenv: bytes,
) -> Mapping[str, tuple[Any, ...]]:
    expected = dict(source_entries)
    version = EXPECTED_AZURE_CLI_FORMULAE["azure-cli"]
    relative = f"Cellar/azure-cli/{version}/libexec/pyvenv.cfg"
    replaced = source_pyvenv.replace(
        str(EXPECTED_HOMEBREW_ROOT).encode(),
        str(runtime_directory / "homebrew").encode(),
    )
    if replaced == source_pyvenv:
        raise RecoveryPlanError("Azure CLI Python environment is not relocatable")
    source_record = expected.get(relative)
    if source_record is None or source_record[0] != "file":
        raise RecoveryPlanError("Azure CLI Python environment is incomplete")
    expected[relative] = (
        "file",
        source_record[1],
        len(replaced),
        _sha256_bytes(replaced),
    )
    for binary, (size, sha256) in EXPECTED_PATCHED_PYTHON_BINARIES.items():
        source_record = expected.get(binary)
        if source_record is None or source_record[:2] != ("file", True):
            raise RecoveryPlanError("Azure CLI Python runtime is incomplete")
        expected[binary] = ("file", True, size, sha256)
    signature_directory = (
        "Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/"
        "Resources/Python.app/Contents/_CodeSignature"
    )
    expected[signature_directory] = ("directory",)
    expected[f"{signature_directory}/CodeResources"] = (
        "file",
        False,
        2_706,
        "7d4ec6cd1f3d7672036476d3383df692b89db41c1d03b4a4c0744f4a76785c58",
    )
    return expected


def _write_existing_snapshot_file(path: Path, value: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_TRUNC
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        offset = 0
        while offset < len(value):
            offset += os.write(descriptor, value[offset:])
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    except OSError as exc:
        raise RecoveryPlanError("Azure CLI snapshot could not be relocated") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _patch_snapshot_python(homebrew: Path) -> None:
    source_dependency = (
        "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/"
        "Python.framework/Versions/3.14/Python"
    )
    replacements = {
        next(
            name
            for name in EXPECTED_PATCHED_PYTHON_BINARIES
            if name.endswith("bin/python3.14")
        ): "@executable_path/../Python",
        next(
            name
            for name in EXPECTED_PATCHED_PYTHON_BINARIES
            if name.endswith("Contents/MacOS/Python")
        ): "@executable_path/../../../../Python",
    }
    environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}
    for relative, replacement in replacements.items():
        binary = homebrew / relative
        for command in (
            [
                "/usr/bin/install_name_tool",
                "-change",
                source_dependency,
                replacement,
                str(binary),
            ],
            ["/usr/bin/codesign", "--force", "--sign", "-", str(binary)],
        ):
            try:
                result = subprocess.run(
                    command,
                    cwd=homebrew,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RecoveryPlanError(
                    "Azure CLI Python runtime could not be relocated"
                ) from exc
            if result.returncode != 0:
                raise RecoveryPlanError(
                    "Azure CLI Python runtime could not be relocated"
                )


def _harden_runtime_tree(root: Path) -> None:
    for directory, names, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            path = Path(directory) / name
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise RecoveryPlanError("Azure CLI snapshot contains an unsafe file")
            os.chmod(path, 0o500 if metadata.st_mode & 0o111 else 0o400)
        for name in names:
            path = Path(directory) / name
            if not path.is_symlink():
                os.chmod(path, 0o500)
    os.chmod(root, 0o500)


def _snapshot_azure_cli_closure(
    runtime_directory: Path,
) -> tuple[Path, str]:
    source_entries = _azure_cli_closure_entries(EXPECTED_HOMEBREW_ROOT, snapshot=False)
    if _runtime_entries_sha256(source_entries) != EXPECTED_AZURE_CLI_CLOSURE_SHA256:
        raise RecoveryPlanError("Azure CLI runtime closure is not pinned")
    snapshot_homebrew = runtime_directory / "homebrew"
    pyvenv_relative = f"Cellar/azure-cli/{EXPECTED_AZURE_CLI_FORMULAE['azure-cli']}/libexec/pyvenv.cfg"
    source_pyvenv = bytearray()
    if _runtime_file_record(EXPECTED_HOMEBREW_ROOT / pyvenv_relative, label="Azure CLI Python environment", capture=source_pyvenv) != source_entries.get(pyvenv_relative):  # fmt: skip
        raise RecoveryPlanError("Azure CLI Python environment changed after manifest")
    if not snapshot_homebrew.exists():
        snapshot_homebrew.mkdir(mode=0o700)
        (snapshot_homebrew / "Cellar").mkdir(mode=0o700)
        (snapshot_homebrew / "opt").mkdir(mode=0o700)
        for formula, version in sorted(EXPECTED_AZURE_CLI_FORMULAE.items()):
            source = EXPECTED_HOMEBREW_ROOT / "Cellar" / formula / version
            destination = snapshot_homebrew / "Cellar" / formula / version
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copytree(
                source,
                destination,
                symlinks=True,
                copy_function=shutil.copy,
            )
            os.symlink(
                f"../Cellar/{formula}/{version}", snapshot_homebrew / "opt" / formula
            )
        pyvenv = (
            snapshot_homebrew
            / "Cellar"
            / "azure-cli"
            / EXPECTED_AZURE_CLI_FORMULAE["azure-cli"]
            / "libexec"
            / "pyvenv.cfg"
        )
        relocated = bytes(source_pyvenv).replace(
            str(EXPECTED_HOMEBREW_ROOT).encode(), str(snapshot_homebrew).encode()
        )
        _write_existing_snapshot_file(pyvenv, relocated)
        _patch_snapshot_python(snapshot_homebrew)
        _harden_runtime_tree(snapshot_homebrew)
    expected_entries = _expected_azure_cli_snapshot_entries(
        source_entries, runtime_directory, bytes(source_pyvenv)
    )
    snapshot_entries = _azure_cli_closure_entries(snapshot_homebrew, snapshot=True)
    if snapshot_entries != expected_entries:
        raise RecoveryPlanError("Azure CLI runtime snapshot is not exact")
    if (
        _runtime_entries_sha256(
            _azure_cli_closure_entries(EXPECTED_HOMEBREW_ROOT, snapshot=False)
        )
        != EXPECTED_AZURE_CLI_CLOSURE_SHA256
    ):
        raise RecoveryPlanError("Azure CLI runtime changed during snapshot")
    wrapper = runtime_directory / "az"
    wrapper_bytes = _azure_cli_wrapper(runtime_directory)
    wrapper_sha256 = _sha256_bytes(wrapper_bytes)
    if not wrapper.exists():
        _write_protected_file(wrapper, wrapper_bytes, label="Azure CLI wrapper")
        os.chmod(wrapper, 0o500)
    _validate_runtime_snapshot(
        wrapper, label="Azure CLI wrapper", expected_sha256=wrapper_sha256
    )
    return wrapper, _runtime_entries_sha256(snapshot_entries)


def _provider_tree_sha256(data_dir: Path) -> str:
    root = data_dir / "providers"
    paths = sorted(root.rglob("terraform-provider-*"))
    if len(paths) != 2 or any(path.is_symlink() for path in paths):
        raise RecoveryPlanError("fresh provider executable set is not exact")
    return _canonical_file_set_sha256(
        {
            str(path.relative_to(root)): bytes.fromhex(
                _executable_sha256(path, label="provider executable")
            )
            for path in paths
        }
    )


def _read_protected_file(
    path: Path, *, label: str, required_mode: int | None = None
) -> bytes:
    _reject_symlink_components(path.parent, label=f"{label} directory")
    parent_descriptor = _open_protected_directory(
        path.parent, label=f"{label} directory"
    )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise RecoveryPlanError(f"{label} is unavailable") from exc
    finally:
        os.close(parent_descriptor)
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if not stat.S_ISREG(before.st_mode):
            raise RecoveryPlanError(f"{label} must be a regular file")
        if before.st_uid != os.geteuid():
            raise RecoveryPlanError(f"{label} is not owned by the current operator")
        if before.st_nlink != 1:
            raise RecoveryPlanError(f"{label} must have exactly one hard link")
        if mode not in {0o400, 0o600} or (
            required_mode is not None and mode != required_mode
        ):
            expected = f"{required_mode:04o}" if required_mode else "0400 or 0600"
            raise RecoveryPlanError(f"{label} must have mode {expected}")
        if before.st_size <= 0 or before.st_size > MAXIMUM_ARTIFACT_BYTES:
            raise RecoveryPlanError(f"{label} size is outside the accepted range")

        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if len(value) != before.st_size or identity_before != identity_after:
            raise RecoveryPlanError(f"{label} changed while it was being read")
        return value
    except OSError as exc:
        raise RecoveryPlanError(f"{label} could not be read") from exc
    finally:
        os.close(descriptor)


def _write_protected_file(path: Path, value: bytes, *, label: str) -> None:
    _reject_symlink_components(path, label=label, allow_missing_leaf=True)
    parent_descriptor = _open_protected_directory(
        path.parent, label=f"{label} directory"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path.name, flags, 0o400, dir_fd=parent_descriptor)
        created = True
        offset = 0
        while offset < len(value):
            offset += os.write(descriptor, value[offset:])
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
    except OSError as exc:
        if created:
            try:
                os.unlink(path.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise RecoveryPlanError(f"{label} could not be written") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)


def _load_json_bytes(value: bytes, *, label: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryPlanError(f"{label} is not valid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise RecoveryPlanError(f"{label} must contain a JSON object")
    return decoded


def _normalized_environment(*, data_dir: Path | None = None) -> dict[str, str]:
    try:
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    except (KeyError, OSError) as exc:
        raise RecoveryPlanError("the operator home directory is unavailable") from exc
    if not home.is_absolute() or home != Path(os.path.abspath(home)):
        raise RecoveryPlanError("the operator home directory is invalid")
    runtime_directory = (
        data_dir / TRUSTED_RUNTIME_DIRECTORY_NAME if data_dir is not None else None
    )
    azure_cli_directory = (
        runtime_directory
        if runtime_directory is not None and runtime_directory.is_dir()
        else EXPECTED_AZURE_CLI_PATH.parent
    )
    environment = {
        "ARM_ENVIRONMENT": "public",
        "ARM_SUBSCRIPTION_ID": EXPECTED_SUBSCRIPTION_ID,
        "ARM_TENANT_ID": EXPECTED_TENANT_ID,
        "ARM_USE_AZUREAD": "true",
        "ARM_USE_CLI": "true",
        "AZURE_CORE_COLLECT_TELEMETRY": "false",
        "AZURE_CORE_DISABLE_CONFIRM_PROMPT": "true",
        "AZURE_CORE_ONLY_SHOW_ERRORS": "true",
        "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no",
        "AZURE_LOGGING_ENABLE_LOG_FILE": "false",
        "CHECKPOINT_DISABLE": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": f"{azure_cli_directory}:/usr/bin:/bin",
        "TF_CLI_CONFIG_FILE": os.devnull,
        "TF_IN_AUTOMATION": "1",
        "TF_INPUT": "0",
    }
    if data_dir is not None:
        azure_config_directory, azure_config_binding = _validated_azure_config(home)
        environment.update({"AZURE_CONFIG_DIR": str(azure_config_directory), AZURE_CONFIG_BINDING_ENV: azure_config_binding})  # noqa: E702  # fmt: skip
        environment["TF_DATA_DIR"] = str(data_dir)
        if runtime_directory is not None and runtime_directory.is_dir():
            environment.update(_azure_cli_environment(runtime_directory))
    ssh_auth_sock = os.environ.get("SSH_AUTH_SOCK")
    if ssh_auth_sock:
        try:
            socket_path = Path(ssh_auth_sock).resolve(strict=True)
            metadata = os.stat(socket_path, follow_symlinks=False)
        except OSError:
            pass
        else:
            if stat.S_ISSOCK(metadata.st_mode) and metadata.st_uid == os.geteuid():
                environment["SSH_AUTH_SOCK"] = str(socket_path)
    return environment


def _run_process(
    arguments: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    pass_fds: Sequence[int] = (),
    max_output_bytes: int = 8 * 1024 * 1024,
    data_dir: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        process = subprocess.Popen(
            list(arguments),
            cwd=cwd,
            env=_normalized_environment(data_dir=data_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            pass_fds=tuple(pass_fds),
            umask=0o077,
            start_new_session=True,
        )
    except OSError as exc:
        raise RecoveryPlanError("required local command did not complete") from exc
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    streams: dict[int, list[bytes]] = {stdout_fd: [], stderr_fd: []}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, stdout_fd)
    selector.register(process.stderr, selectors.EVENT_READ, stderr_fd)
    deadline = time.monotonic() + timeout_seconds
    total = 0
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RecoveryPlanError("required local command timed out")
            for key, _ in selector.select(min(remaining, 0.5)):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > max_output_bytes:
                    raise RecoveryPlanError(
                        "required local command output exceeded limit"
                    )
                streams[key.data].append(chunk)
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired, RecoveryPlanError) as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.stdout.close()
        process.stderr.close()
        process.wait()
        if isinstance(exc, RecoveryPlanError):
            raise
        raise RecoveryPlanError("required local command did not complete") from exc
    finally:
        selector.close()
    stdout = b"".join(streams[stdout_fd])
    stderr = b"".join(streams[stderr_fd])
    process.stdout.close()
    process.stderr.close()
    return subprocess.CompletedProcess(list(arguments), returncode, stdout, stderr)


def _verify_tofu_version(tofu: Path, *, data_dir: Path | None = None) -> None:
    result = _run_process(
        [str(tofu), "version", "-json"],
        cwd=REPOSITORY_ROOT,
        timeout_seconds=30,
        data_dir=data_dir,
    )
    if result.returncode != 0 or not result.stdout:
        raise RecoveryPlanError("the OpenTofu version could not be verified")
    version = _load_json_bytes(result.stdout, label="OpenTofu version output")
    if version.get("terraform_version") != EXPECTED_TOFU_VERSION:
        raise RecoveryPlanError("the OpenTofu executable version is not pinned")


def _resolve_and_verify_tofu() -> Path:
    tofu = _trusted_executable_source(
        EXPECTED_TOFU_PATH,
        label="pinned OpenTofu",
        expected_sha256=EXPECTED_TOFU_SHA256,
    )
    _verify_tofu_version(tofu)
    return tofu


def _snapshot_and_verify_runtime(*, data_dir: Path) -> RuntimeSnapshot:
    previous_umask = os.umask(0o077)
    try:
        return _snapshot_and_verify_runtime_with_protected_umask(data_dir=data_dir)
    finally:
        os.umask(previous_umask)


def _snapshot_and_verify_runtime_with_protected_umask(
    *, data_dir: Path
) -> RuntimeSnapshot:
    _require_protected_directory(data_dir, label="OpenTofu data directory")
    runtime_directory = data_dir / TRUSTED_RUNTIME_DIRECTORY_NAME
    if runtime_directory.exists():
        _require_protected_directory(
            runtime_directory, label="trusted runtime directory"
        )
    else:
        _create_protected_directory(
            runtime_directory, label="trusted runtime directory"
        )
    _remove_stale_azure_config_snapshots(runtime_directory)
    for name in ("azure-extensions",):
        directory = runtime_directory / name
        if directory.exists():
            _require_protected_directory(directory, label=f"trusted {name}")
        else:
            _create_protected_directory(directory, label=f"trusted {name}")
        if any(directory.iterdir()):
            raise RecoveryPlanError(f"trusted {name} directory is not empty")
    _prepare_and_verify_runtime_home(runtime_directory, create_missing=True)
    tofu = _snapshot_executable(
        EXPECTED_TOFU_PATH,
        runtime_directory / "tofu",
        label="OpenTofu",
        expected_sha256=EXPECTED_TOFU_SHA256,
    )
    _trusted_executable_source(
        EXPECTED_AZURE_CLI_PATH,
        label="pinned Azure CLI entrypoint",
        expected_sha256=EXPECTED_AZURE_CLI_SHA256,
    )
    azure_cli, azure_cli_closure_sha256 = _snapshot_azure_cli_closure(runtime_directory)
    try:
        runtime_names = {entry.name for entry in runtime_directory.iterdir()}
    except OSError as exc:
        raise RecoveryPlanError("trusted runtime directory is unavailable") from exc
    if runtime_names != {
        AZURE_CONFIG_LOCK_NAME,
        "az",
        "azure-extensions",
        "azure-home",
        "homebrew",
        "tofu",
    }:
        raise RecoveryPlanError("trusted runtime executable set is not exact")
    _verify_tofu_version(tofu, data_dir=data_dir)
    azure_version = _run_process(
        [str(azure_cli), "version"],
        cwd=REPOSITORY_ROOT,
        timeout_seconds=60,
        max_output_bytes=1024 * 1024,
        data_dir=data_dir,
    )
    if azure_version.returncode != 0 or not azure_version.stdout:
        raise RecoveryPlanError("the Azure CLI version could not be verified")
    azure_version_document = _load_json_bytes(
        azure_version.stdout, label="Azure CLI version output"
    )
    azure_version_sha256 = _sha256_bytes(_canonical_json_bytes(azure_version_document))
    if azure_version_sha256 != EXPECTED_AZURE_CLI_VERSION_SHA256:
        raise RecoveryPlanError("the Azure CLI version manifest is not pinned")
    azure_cloud = _run_process(
        [str(azure_cli), "cloud", "show", "--name", "AzureCloud"],
        cwd=REPOSITORY_ROOT,
        timeout_seconds=60,
        max_output_bytes=1024 * 1024,
        data_dir=data_dir,
    )
    if azure_cloud.returncode != 0 or _sha256_bytes(_canonical_json_bytes(_load_json_bytes(azure_cloud.stdout, label="Azure cloud output"))) != EXPECTED_AZURE_CLOUD_SHA256:  # fmt: skip
        raise RecoveryPlanError("the Azure cloud manifest is not pinned")
    _prepare_and_verify_runtime_home(runtime_directory, create_missing=False)
    _validate_runtime_snapshot(
        tofu, label="OpenTofu", expected_sha256=EXPECTED_TOFU_SHA256
    )
    wrapper_sha256 = _sha256_bytes(_azure_cli_wrapper(runtime_directory))
    _validate_runtime_snapshot(
        azure_cli, label="Azure CLI wrapper", expected_sha256=wrapper_sha256
    )
    runtime_snapshot_sha256 = _sha256_bytes(
        _canonical_json_bytes(
            {
                "azure_cli_closure_sha256": azure_cli_closure_sha256,
                "azure_cli_config_policy": "private-config-dedicated-home-v3",
                "azure_cloud_sha256": EXPECTED_AZURE_CLOUD_SHA256,
                "azure_cli_wrapper_sha256": wrapper_sha256,
                "open_tofu_sha256": EXPECTED_TOFU_SHA256,
            }
        )
    )
    return RuntimeSnapshot(
        tofu=tofu,
        azure_cli=azure_cli,
        tofu_sha256=EXPECTED_TOFU_SHA256,
        azure_cli_sha256=EXPECTED_AZURE_CLI_SHA256,
        azure_cli_version_sha256=azure_version_sha256,
        azure_cli_closure_sha256=azure_cli_closure_sha256,
        runtime_snapshot_sha256=runtime_snapshot_sha256,
    )


def _show_saved_plan(plan_bytes: bytes, *, tofu: Path, data_dir: Path) -> bytes:
    with tempfile.TemporaryFile(prefix="cosmaneura-recovery-plan-") as plan_file:
        plan_file.write(plan_bytes)
        plan_file.flush()
        plan_file.seek(0)
        descriptor = plan_file.fileno()
        result = _run_process(
            [
                str(tofu),
                f"-chdir={REHEARSAL_ROOT}",
                "show",
                "-json",
                f"/dev/fd/{descriptor}",
            ],
            cwd=REPOSITORY_ROOT,
            timeout_seconds=120,
            pass_fds=(descriptor,),
            max_output_bytes=MAXIMUM_PLAN_JSON_BYTES,
            data_dir=data_dir,
        )
    if result.returncode != 0:
        raise RecoveryPlanError("the saved plan could not be decoded by OpenTofu")
    if not result.stdout or len(result.stdout) > MAXIMUM_PLAN_JSON_BYTES:
        raise RecoveryPlanError("derived plan JSON size is outside the accepted range")
    return result.stdout


def _show_state(state_bytes: bytes, *, tofu: Path, data_dir: Path) -> bytes:
    with tempfile.TemporaryFile(prefix="cosmaneura-recovery-state-") as state_file:
        state_file.write(state_bytes)
        state_file.flush()
        state_file.seek(0)
        descriptor = state_file.fileno()
        result = _run_process(
            [
                str(tofu),
                f"-chdir={REHEARSAL_ROOT}",
                "show",
                "-json",
                f"/dev/fd/{descriptor}",
            ],
            cwd=REPOSITORY_ROOT,
            timeout_seconds=120,
            pass_fds=(descriptor,),
            max_output_bytes=MAXIMUM_PLAN_JSON_BYTES,
            data_dir=data_dir,
        )
    if result.returncode != 0:
        raise RecoveryPlanError("the state projection could not be decoded by OpenTofu")
    if not result.stdout or len(result.stdout) > MAXIMUM_PLAN_JSON_BYTES:
        raise RecoveryPlanError("derived state JSON size is outside the accepted range")
    return result.stdout


def _generate_saved_plan(*, tofu: Path, output_parent: Path, data_dir: Path) -> bytes:
    with tempfile.TemporaryDirectory(
        prefix=".cosmaneura-recovery-plan-", dir=output_parent
    ) as temporary_directory:
        candidate = Path(temporary_directory) / "candidate.tfplan"
        result = _run_process(
            [
                str(tofu),
                f"-chdir={REHEARSAL_ROOT}",
                "plan",
                "-input=false",
                "-lock=true",
                "-lock-timeout=5m",
                "-refresh=true",
                "-detailed-exitcode",
                "-no-color",
                f"-out={candidate}",
                *PLAN_VARIABLE_ARGUMENTS,
            ],
            cwd=REPOSITORY_ROOT,
            timeout_seconds=900,
            data_dir=data_dir,
        )
        if result.returncode != 2:
            raise RecoveryPlanError(
                "the fixed full-refresh plan did not produce the expected change set"
            )
        return _read_protected_file(
            candidate, label="generated saved plan", required_mode=0o600
        )


def _trusted_system_executable(expected: Path, *, label: str) -> Path:
    try:
        path = expected.resolve(strict=True)
        metadata = path.stat()
    except OSError as exc:
        raise RecoveryPlanError(
            f"the system {label} executable is unavailable"
        ) from exc
    if (
        path != expected
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise RecoveryPlanError(f"the system {label} executable is not trusted")
    return path


def _run_git(arguments: Sequence[str], *, repository: bool = True) -> bytes:
    command = [str(_trusted_system_executable(SYSTEM_GIT, label="Git")), "--no-replace-objects"]  # fmt: skip
    if repository:
        command.extend(("-C", str(REPOSITORY_ROOT)))
        result = _run_process([*command, *arguments], cwd=REPOSITORY_ROOT, timeout_seconds=30)  # fmt: skip
    else:
        ssh = _trusted_system_executable(SYSTEM_SSH, label="SSH")
        with tempfile.NamedTemporaryFile(mode="w", encoding="ascii") as known_hosts:
            known_hosts.write(f"{GITHUB_HOST_KEY}\n")
            known_hosts.flush()
            os.fchmod(known_hosts.fileno(), 0o400)
            ssh_command = shlex.join([str(ssh), "-F", os.devnull, "-oBatchMode=yes", "-oCanonicalizeHostname=no", "-oHostName=github.com", "-oPort=22", "-oProxyCommand=none", "-oProxyJump=none", "-oStrictHostKeyChecking=yes", f"-oUserKnownHostsFile={known_hosts.name}", "-oGlobalKnownHostsFile=/dev/null", "-oHostKeyAlgorithms=ssh-ed25519"])  # fmt: skip
            result = _run_process([*command, "-c", f"core.sshCommand={ssh_command}", *arguments], cwd=Path("/"), timeout_seconds=30)  # fmt: skip
    if result.returncode != 0:
        raise RecoveryPlanError("the source checkout could not be verified")
    return result.stdout


def _source_snapshot(expected_commit: str) -> SourceSnapshot:
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
        raise RecoveryPlanError("expected source commit is not a full lowercase SHA")
    head = _run_git(["rev-parse", "HEAD"]).decode("ascii").strip()
    remote_head = (
        _run_git(["rev-parse", "refs/remotes/origin/master"]).decode("ascii").strip()
    )
    origin = _run_git(["remote", "get-url", "--all", "origin"]).decode("ascii")
    live_remote = _run_git(
        ["ls-remote", EXPECTED_ORIGIN_URL, "refs/heads/master"], repository=False
    ).decode("ascii")
    if (
        head != expected_commit
        or remote_head != expected_commit
        or origin != f"{EXPECTED_ORIGIN_URL}\n"
        or live_remote != f"{expected_commit}\trefs/heads/master\n"
    ):
        raise RecoveryPlanError(
            "source checkout is not the expected protected-master commit"
        )
    status = _run_git(
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            str(REHEARSAL_ROOT.relative_to(REPOSITORY_ROOT)),
            str(VALIDATOR_PATH.relative_to(REPOSITORY_ROOT)),
        ]
    )
    if status:
        raise RecoveryPlanError("recovery source paths are not clean")

    terraform_files: dict[str, bytes] = {}
    for path in sorted(REHEARSAL_ROOT.glob("*.tf")):
        if path.is_symlink() or not path.is_file():
            raise RecoveryPlanError(
                "recovery source contains an invalid Terraform file"
            )
        terraform_files[path.name] = path.read_bytes()
    if not terraform_files:
        raise RecoveryPlanError("recovery Terraform source is missing")
    lock_path = REHEARSAL_ROOT / ".terraform.lock.hcl"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RecoveryPlanError("recovery provider lockfile is missing")
    lockfile = lock_path.read_bytes()
    tree_files = {**terraform_files, ".terraform.lock.hcl": lockfile}
    source_root = REHEARSAL_ROOT.relative_to(REPOSITORY_ROOT)
    expected_names = set(
        _run_git(
            ["ls-tree", "-r", "--name-only", expected_commit, "--", str(source_root)]
        )
        .decode("utf-8")
        .splitlines()
    )
    actual_names = {str(source_root / name) for name in tree_files}
    if actual_names != {
        name for name in expected_names if name.endswith((".tf", ".terraform.lock.hcl"))
    }:
        raise RecoveryPlanError(
            "recovery source file set differs from the expected commit"
        )
    for name, value in tree_files.items():
        if _run_git(["show", f"{expected_commit}:{source_root / name}"]) != value:
            raise RecoveryPlanError(
                "recovery source bytes differ from the expected commit"
            )
    validator_name = str(VALIDATOR_PATH.relative_to(REPOSITORY_ROOT))
    validator_bytes = VALIDATOR_PATH.read_bytes()
    if _run_git(["show", f"{expected_commit}:{validator_name}"]) != validator_bytes:
        raise RecoveryPlanError("recovery validator differs from the expected commit")
    tree_files[validator_name] = validator_bytes
    return SourceSnapshot(
        commit=head,
        terraform_files=terraform_files,
        lockfile=lockfile,
        tree_sha256=_canonical_file_set_sha256(tree_files),
    )


def _initialize_tofu(*, tofu: Path, data_dir: Path) -> None:
    arguments = [
        str(tofu),
        f"-chdir={REHEARSAL_ROOT}",
        "init",
        "-backend=true",
        "-input=false",
        "-lockfile=readonly",
        "-reconfigure",
        *(
            f"-backend-config={name}={str(value).lower()}"
            for name, value in EXPECTED_BACKEND_CONFIG.items()
        ),
    ]
    result = _run_process(
        arguments, cwd=REPOSITORY_ROOT, timeout_seconds=300, data_dir=data_dir
    )
    if result.returncode != 0:
        raise RecoveryPlanError("the isolated backend could not be initialized")


def _backend_snapshot(*, tofu: Path, data_dir: Path) -> str:
    path = data_dir / "terraform.tfstate"
    value = _read_protected_file(path, label="backend metadata", required_mode=0o600)
    document = _load_json_bytes(value, label="backend metadata")
    backend = document.get("backend")
    if not isinstance(backend, Mapping) or backend.get("type") != "azurerm":
        raise RecoveryPlanError("backend type is not the isolated AzureRM backend")
    config = backend.get("config")
    if not isinstance(config, Mapping):
        raise RecoveryPlanError("backend configuration is missing")
    for name, expected in EXPECTED_BACKEND_CONFIG.items():
        if config.get(name) != expected:
            raise RecoveryPlanError("backend coordinates are not exact")
    for name, setting in config.items():
        if name not in EXPECTED_BACKEND_CONFIG and setting not in (None, "", False):
            raise RecoveryPlanError("backend configuration contains an override")
    workspace = _run_process(
        [str(tofu), f"-chdir={REHEARSAL_ROOT}", "workspace", "show"],
        cwd=REPOSITORY_ROOT,
        timeout_seconds=30,
        max_output_bytes=1024,
        data_dir=data_dir,
    )
    if workspace.returncode != 0 or workspace.stdout.strip() != b"default":
        raise RecoveryPlanError("the isolated backend workspace is not default")
    return _sha256_bytes(value)


def _normalize_boolean(value: Any) -> Any:
    if isinstance(value, str) and value in {"true", "false"}:
        return value == "true"
    return value


def _bounded_names(values: set[str]) -> str:
    names = sorted(values)
    displayed = names[:8]
    suffix = "" if len(names) <= len(displayed) else f" (+{len(names) - 8} more)"
    return ", ".join(displayed) + suffix


def _state_address(resource: Mapping[str, Any], instance: Mapping[str, Any]) -> str:
    mode = resource.get("mode")
    resource_type = resource.get("type")
    name = resource.get("name")
    module = resource.get("module")
    if mode not in {"managed", "data"}:
        raise RecoveryPlanError("state contains a resource with an invalid mode")
    if not isinstance(resource_type, str) or not isinstance(name, str):
        raise RecoveryPlanError("state contains a resource with an invalid address")
    address = f"{resource_type}.{name}"
    if mode == "data":
        address = f"data.{address}"
    if module is not None:
        if not isinstance(module, str):
            raise RecoveryPlanError("state contains an invalid module address")
        address = f"{module}.{address}"
    if "index_key" in instance:
        key = instance["index_key"]
        if isinstance(key, bool) or not isinstance(key, (str, int)):
            raise RecoveryPlanError("state contains an invalid instance key")
        address = f"{address}[{json.dumps(key, separators=(',', ':'))}]"
    return address


def _validate_state(
    state: Mapping[str, Any],
    *,
    label: str,
    expected_serial: int = EXPECTED_RECOVERY_SERIAL,
) -> tuple[str, int, Mapping[str, tuple[str, int, str]]]:
    if state.get("lineage") != EXPECTED_LINEAGE:
        raise RecoveryPlanError(
            f"{label} lineage does not match the isolated rehearsal"
        )
    serial = state.get("serial")
    if type(serial) is not int or serial != expected_serial:
        raise RecoveryPlanError(f"{label} serial must be exactly {expected_serial}")
    if state.get("terraform_version") != EXPECTED_TOFU_VERSION:
        raise RecoveryPlanError(f"{label} OpenTofu version is not pinned")

    resources = state.get("resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
        raise RecoveryPlanError(f"{label} resources are invalid")
    inventory: dict[str, tuple[str, int, str]] = {}
    for resource in resources:
        if not isinstance(resource, Mapping):
            raise RecoveryPlanError(f"{label} contains an invalid resource")
        instances = resource.get("instances")
        if not isinstance(instances, Sequence) or isinstance(instances, (str, bytes)):
            raise RecoveryPlanError(f"{label} contains invalid instances")
        provider = resource.get("provider")
        if not isinstance(provider, str):
            raise RecoveryPlanError(f"{label} contains an invalid provider address")
        for instance in instances:
            if not isinstance(instance, Mapping):
                raise RecoveryPlanError(f"{label} contains an invalid instance")
            if instance.get("status") not in {None, "ready"}:
                raise RecoveryPlanError(f"{label} contains a non-ready instance")
            if instance.get("deposed") is not None:
                raise RecoveryPlanError(f"{label} contains a deposed instance")
            schema_version = instance.get("schema_version")
            if isinstance(schema_version, bool) or not isinstance(schema_version, int):
                raise RecoveryPlanError(f"{label} contains an invalid schema version")
            attributes = instance.get("attributes")
            if not isinstance(attributes, Mapping):
                raise RecoveryPlanError(f"{label} contains invalid attributes")
            resource_id = attributes.get("id")
            if not isinstance(resource_id, str) or not resource_id:
                raise RecoveryPlanError(f"{label} contains an invalid resource ID")
            address = _state_address(resource, instance)
            if address in inventory:
                raise RecoveryPlanError(f"{label} contains duplicate addresses")
            inventory[address] = (provider, schema_version, resource_id)

    address_set = set(inventory)
    if address_set != EXPECTED_EXISTING_ADDRESSES:
        missing = set(EXPECTED_EXISTING_ADDRESSES) - address_set
        extra = address_set - set(EXPECTED_EXISTING_ADDRESSES)
        raise RecoveryPlanError(
            f"{label} address set differs from the recovery inventory; "
            f"missing={_bounded_names(missing)}; extra={_bounded_names(extra)}"
        )
    if set(EXPECTED_STATE_IDENTITIES) != EXPECTED_EXISTING_ADDRESSES:
        raise RecoveryPlanError("internal recovery state identity contract is invalid")
    for address, expected_identity in EXPECTED_STATE_IDENTITIES.items():
        if inventory[address] != expected_identity:
            raise RecoveryPlanError(
                f"{label} resource identity does not match the exact contract: "
                f"{address}"
            )
    return EXPECTED_LINEAGE, serial, inventory


def _semantic_state_sha256(state: Mapping[str, Any], *, label: str) -> str:
    check_results = state.get("check_results")
    if check_results is not None and (
        not isinstance(check_results, Sequence)
        or isinstance(check_results, (str, bytes))
    ):
        raise RecoveryPlanError(f"{label} check results are invalid")
    semantic_state = dict(state)
    semantic_state.pop("check_results", None)
    return _sha256_bytes(_canonical_json_bytes(semantic_state))


def _read_zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise RecoveryPlanError(
            "saved plan archive is missing required content"
        ) from exc
    if info.flag_bits & 0x1:
        raise RecoveryPlanError("saved plan archive contains encrypted content")
    file_type = (info.external_attr >> 16) & 0o170000
    if file_type == stat.S_IFLNK:
        raise RecoveryPlanError("saved plan archive contains a symbolic link")
    if info.file_size <= 0 or info.file_size > MAXIMUM_ARTIFACT_BYTES:
        raise RecoveryPlanError("saved plan archive member size is invalid")
    try:
        value = archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise RecoveryPlanError("saved plan archive could not be read") from exc
    if len(value) != info.file_size:
        raise RecoveryPlanError("saved plan archive member is incomplete")
    return value


def _inspect_plan_archive(
    plan_bytes: bytes, *, source: SourceSnapshot
) -> tuple[bytes, Mapping[str, Any]]:
    try:
        with zipfile.ZipFile(io.BytesIO(plan_bytes)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise RecoveryPlanError("saved plan archive contains duplicate members")
            for info in infos:
                file_type = (info.external_attr >> 16) & 0o170000
                if info.flag_bits & 0x1 or file_type == stat.S_IFLNK:
                    raise RecoveryPlanError("saved plan archive member is unsafe")
                if info.file_size <= 0 or info.file_size > MAXIMUM_ARTIFACT_BYTES:
                    raise RecoveryPlanError("saved plan archive member size is invalid")
            if sum(info.file_size for info in infos) > MAXIMUM_PLAN_ARCHIVE_BYTES:
                raise RecoveryPlanError(
                    "saved plan archive expands beyond the size limit"
                )
            expected_config_names = {
                f"tfconfig/m-/{name}" for name in source.terraform_files
            }
            expected_names = expected_config_names | {
                "tfplan",
                "tfstate",
                "tfstate-prev",
                "tfconfig/modules.json",
                ".terraform.lock.hcl",
            }
            if set(names) != expected_names:
                raise RecoveryPlanError(
                    "saved plan archive contents differ from the pinned root"
                )
            if archive.testzip() is not None:
                raise RecoveryPlanError("saved plan archive failed its CRC check")
            try:
                modules = json.loads(_read_zip_member(archive, "tfconfig/modules.json"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RecoveryPlanError(
                    "saved plan module manifest is invalid"
                ) from exc
            if modules != [{"Key": "", "Dir": "."}]:
                raise RecoveryPlanError("saved plan module manifest is not exact")
            for name, expected in source.terraform_files.items():
                if _read_zip_member(archive, f"tfconfig/m-/{name}") != expected:
                    raise RecoveryPlanError(
                        "saved plan Terraform source differs from the reviewed checkout"
                    )
            if _read_zip_member(archive, ".terraform.lock.hcl") != source.lockfile:
                raise RecoveryPlanError(
                    "saved plan provider lockfile differs from the reviewed checkout"
                )
            embedded_state_bytes = _read_zip_member(archive, "tfstate")
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise RecoveryPlanError(
            "saved plan is not a valid OpenTofu plan archive"
        ) from exc
    embedded_state = _load_json_bytes(
        embedded_state_bytes, label="saved plan embedded state"
    )
    return embedded_state_bytes, embedded_state


def _module_addresses(module: Mapping[str, Any]) -> set[str]:
    addresses: set[str] = set()
    resources = module.get("resources", [])
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
        raise RecoveryPlanError("plan prior-state resources are invalid")
    for resource in resources:
        if not isinstance(resource, Mapping):
            raise RecoveryPlanError("plan prior state contains an invalid resource")
        address = resource.get("address")
        if not isinstance(address, str):
            raise RecoveryPlanError("plan prior state contains an invalid address")
        addresses.add(address)
    children = module.get("child_modules", [])
    if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
        raise RecoveryPlanError("plan prior-state child modules are invalid")
    for child in children:
        if not isinstance(child, Mapping):
            raise RecoveryPlanError("plan prior state contains an invalid module")
        addresses.update(_module_addresses(child))
    return addresses


def _validate_provider_configuration(plan: Mapping[str, Any]) -> None:
    configuration = plan.get("configuration")
    if not isinstance(configuration, Mapping):
        raise RecoveryPlanError("plan configuration is missing")
    provider_config = configuration.get("provider_config")
    if not isinstance(provider_config, Mapping):
        raise RecoveryPlanError("plan provider configuration is missing")
    if set(provider_config) != {"azurerm", "azapi", "terraform"}:
        raise RecoveryPlanError("plan provider set is not exact")
    azurerm = provider_config.get("azurerm")
    azapi = provider_config.get("azapi")
    builtin = provider_config.get("terraform")
    if not all(isinstance(value, Mapping) for value in (azurerm, azapi, builtin)):
        raise RecoveryPlanError("plan provider configuration is invalid")
    assert isinstance(azurerm, Mapping)
    assert isinstance(azapi, Mapping)
    assert isinstance(builtin, Mapping)
    expected_providers = {
        "azurerm": (azurerm, "registry.opentofu.org/hashicorp/azurerm", "4.73.0"),
        "azapi": (azapi, "registry.opentofu.org/azure/azapi", "2.1.0"),
        "terraform": (builtin, "terraform.io/builtin/terraform", None),
    }
    for name, (provider, full_name, version) in expected_providers.items():
        if provider.get("full_name") != full_name:
            raise RecoveryPlanError(f"{name} provider source is not pinned")
        if provider.get("version_constraint") != version:
            raise RecoveryPlanError(f"{name} provider version is not pinned")

    expressions = azurerm.get("expressions")
    if not isinstance(expressions, Mapping):
        raise RecoveryPlanError("AzureRM provider expressions are missing")
    features = expressions.get("features")
    if (
        not isinstance(features, Sequence)
        or isinstance(features, (str, bytes))
        or len(features) != 1
        or not isinstance(features[0], Mapping)
    ):
        raise RecoveryPlanError(
            "AzureRM storage data-plane opt-out is missing from the saved plan"
        )
    feature = features[0]
    assert isinstance(feature, Mapping)
    storage = feature.get("storage")
    if (
        not isinstance(storage, Sequence)
        or isinstance(storage, (str, bytes))
        or len(storage) != 1
        or not isinstance(storage[0], Mapping)
    ):
        raise RecoveryPlanError(
            "AzureRM storage data-plane opt-out is missing from the saved plan"
        )
    storage_feature = storage[0]
    assert isinstance(storage_feature, Mapping)
    data_plane = storage_feature.get("data_plane_available")
    if (
        not isinstance(data_plane, Mapping)
        or data_plane.get("constant_value") is not False
    ):
        raise RecoveryPlanError("AzureRM storage data plane must be disabled")


def _validate_variables(plan: Mapping[str, Any]) -> None:
    variables = plan.get("variables")
    if not isinstance(variables, Mapping):
        raise RecoveryPlanError("plan variables are missing")
    if set(variables) != set(EXPECTED_VARIABLES):
        raise RecoveryPlanError("plan variable names differ from the base recovery set")
    for name, expected in EXPECTED_VARIABLES.items():
        record = variables[name]
        if not isinstance(record, Mapping) or "value" not in record:
            raise RecoveryPlanError(f"plan variable {name} has no fixed value")
        if _normalize_boolean(record["value"]) != expected:
            raise RecoveryPlanError(f"plan variable {name} is not recovery-safe")


def _validate_checks(plan: Mapping[str, Any]) -> None:
    checks = plan.get("checks")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        raise RecoveryPlanError("plan check results are missing")
    if not checks:
        raise RecoveryPlanError("plan contains no evaluated checks")
    for check in checks:
        if not isinstance(check, Mapping) or check.get("status") != "pass":
            raise RecoveryPlanError("plan contains a non-passing check")
        instances = check.get("instances", [])
        if not isinstance(instances, Sequence) or isinstance(instances, (str, bytes)):
            raise RecoveryPlanError("plan contains invalid check instances")
        for instance in instances:
            if not isinstance(instance, Mapping) or instance.get("status") != "pass":
                raise RecoveryPlanError("plan contains a non-passing check instance")
            if instance.get("problems") not in (None, []):
                raise RecoveryPlanError("plan check instance reports a problem")


def _validate_plan_timestamp(plan: Mapping[str, Any], *, now: datetime) -> str:
    raw_timestamp = plan.get("timestamp")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.endswith("Z"):
        raise RecoveryPlanError("plan timestamp is missing or not UTC")
    try:
        timestamp = datetime.fromisoformat(raw_timestamp[:-1] + "+00:00")
    except ValueError as exc:
        raise RecoveryPlanError("plan timestamp is invalid") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise RecoveryPlanError("plan timestamp is not UTC")
    normalized_now = now.astimezone(UTC)
    if timestamp > normalized_now + MAXIMUM_CLOCK_SKEW:
        raise RecoveryPlanError("plan timestamp is in the future")
    if normalized_now - timestamp > MAXIMUM_PLAN_AGE:
        raise RecoveryPlanError("saved plan is stale")
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _resource_change_metadata(address: str) -> Mapping[str, Any]:
    match = re.fullmatch(
        r'(?P<type>[a-z0-9_]+)\.(?P<name>[a-z0-9_]+)\[(?P<index>0|"(?:[^"\\]|\\.)+")\]',
        address,
    )
    if match is None:
        raise RecoveryPlanError("expected recovery resource address is invalid")
    resource_type = match.group("type")
    raw_index = match.group("index")
    index: int | str = int(raw_index) if raw_index.isdigit() else json.loads(raw_index)
    if resource_type == "azapi_resource":
        provider = "registry.opentofu.org/azure/azapi"
    elif resource_type == "terraform_data":
        provider = "terraform.io/builtin/terraform"
    elif resource_type.startswith("azurerm_"):
        provider = "registry.opentofu.org/hashicorp/azurerm"
    else:
        raise RecoveryPlanError("expected recovery resource provider is invalid")
    return {
        "address": address,
        "mode": "managed",
        "type": resource_type,
        "name": match.group("name"),
        "index": index,
        "provider_name": provider,
    }


def _expected_container_change(address: str) -> Mapping[str, Any]:
    metadata = _resource_change_metadata(address)
    name = metadata["index"]
    if not isinstance(name, str):
        raise RecoveryPlanError("expected marker-container index is invalid")
    return {
        **metadata,
        "change": {
            "actions": ["create"],
            "before": None,
            "after": {
                "body": {"properties": {"publicAccess": "None"}},
                "create_headers": None,
                "create_query_parameters": None,
                "delete_headers": None,
                "delete_query_parameters": None,
                "identity": [],
                "ignore_casing": False,
                "ignore_missing_property": True,
                "location": None,
                "locks": None,
                "name": name,
                "parent_id": EXPECTED_MARKER_CONTAINER_PARENT_ID,
                "read_headers": None,
                "read_query_parameters": None,
                "replace_triggers_external_values": None,
                "replace_triggers_refs": None,
                "response_export_values": None,
                "retry": None,
                "schema_validation_enabled": True,
                "tags": None,
                "timeouts": None,
                "type": (
                    "Microsoft.Storage/storageAccounts/blobServices/"
                    "containers@2023-05-01"
                ),
                "update_headers": None,
                "update_query_parameters": None,
            },
            "after_unknown": {
                "body": {"properties": {}},
                "id": True,
                "identity": [],
                "output": True,
            },
            "before_sensitive": False,
            "after_sensitive": {
                "body": {"properties": {}},
                "identity": [],
            },
        },
    }


def _expected_private_endpoint_change(address: str) -> Mapping[str, Any]:
    return {
        **_resource_change_metadata(address),
        "change": {
            "actions": ["create"],
            "before": None,
            "after": {
                "custom_network_interface_name": None,
                "ip_configuration": [],
                "location": "eastus",
                "name": "cosmaneura-staging-rehearsal-marker-pe",
                "private_dns_zone_group": [
                    {
                        "name": "blob",
                        "private_dns_zone_ids": [EXPECTED_MARKER_PRIVATE_DNS_ZONE_ID],
                    }
                ],
                "private_service_connection": [
                    {
                        "is_manual_connection": False,
                        "name": "cosmaneura-staging-rehearsal-marker-connection",
                        "private_connection_resource_alias": None,
                        "private_connection_resource_id": (
                            EXPECTED_MARKER_STORAGE_ACCOUNT_ID
                        ),
                        "request_message": None,
                        "subresource_names": ["blob"],
                    }
                ],
                "resource_group_name": "cosmaneura-staging-rehearsal-control",
                "subnet_id": EXPECTED_MARKER_SUBNET_ID,
                "tags": dict(EXPECTED_VARIABLES["tags"]),
                "timeouts": None,
            },
            "after_unknown": {
                "custom_dns_configs": True,
                "id": True,
                "ip_configuration": [],
                "network_interface": True,
                "private_dns_zone_configs": True,
                "private_dns_zone_group": [
                    {"id": True, "private_dns_zone_ids": [False]}
                ],
                "private_service_connection": [
                    {"private_ip_address": True, "subresource_names": [False]}
                ],
                "tags": {},
            },
            "before_sensitive": False,
            "after_sensitive": {
                "custom_dns_configs": [],
                "ip_configuration": [],
                "network_interface": [],
                "private_dns_zone_configs": [],
                "private_dns_zone_group": [{"private_dns_zone_ids": [False]}],
                "private_service_connection": [{"subresource_names": [False]}],
                "tags": {},
            },
            "after_identity": {
                "name": None,
                "resource_group_name": None,
                "subscription_id": None,
            },
        },
    }


def _expected_role_assignment_change(address: str) -> Mapping[str, Any]:
    try:
        assignment_name, principal_id = EXPECTED_ROLE_ASSIGNMENTS[address]
    except KeyError as exc:
        raise RecoveryPlanError("expected role assignment is invalid") from exc
    return {
        **_resource_change_metadata(address),
        "change": {
            "actions": ["create"],
            "before": None,
            "after": {
                "condition": None,
                "delegated_managed_identity_resource_id": None,
                "description": None,
                "name": assignment_name,
                "principal_id": principal_id,
                "principal_type": "ServicePrincipal",
                "role_definition_id": EXPECTED_MARKER_READER_ROLE_ID,
                "skip_service_principal_aad_check": True,
                "timeouts": None,
            },
            "after_unknown": {
                "condition_version": True,
                "id": True,
                "role_definition_name": True,
                "scope": True,
            },
            "before_sensitive": False,
            "after_sensitive": {},
        },
    }


def _expected_create_change(address: str) -> Mapping[str, Any]:
    if address.startswith("azapi_resource.marker_container["):
        return _expected_container_change(address)
    if address == "azurerm_private_endpoint.marker[0]":
        return _expected_private_endpoint_change(address)
    if address in EXPECTED_ROLE_ASSIGNMENTS:
        return _expected_role_assignment_change(address)
    raise RecoveryPlanError("expected create address has no exact value contract")


def _projected_state_resources(
    state_projection: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Any]]:
    if (
        state_projection.get("format_version") != "1.0"
        or state_projection.get("terraform_version") != EXPECTED_TOFU_VERSION
    ):
        raise RecoveryPlanError("state projection format or runtime is not pinned")
    values = state_projection.get("values")
    if not isinstance(values, Mapping):
        raise RecoveryPlanError("state projection values are missing")
    root_module = values.get("root_module")
    if not isinstance(root_module, Mapping) or set(root_module) != {"resources"}:
        raise RecoveryPlanError("state projection root module is not exact")
    resources = root_module.get("resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
        raise RecoveryPlanError("state projection resources are invalid")
    output: dict[str, Mapping[str, Any]] = {}
    required = {
        "address",
        "mode",
        "type",
        "name",
        "index",
        "provider_name",
        "schema_version",
        "values",
        "sensitive_values",
    }
    optional = {"depends_on", "identity", "identity_schema_version"}
    for resource in resources:
        if not isinstance(resource, Mapping):
            raise RecoveryPlanError("state projection contains an invalid resource")
        address = resource.get("address")
        if not isinstance(address, str) or address in output:
            raise RecoveryPlanError("state projection contains an invalid address")
        keys = set(resource)
        if not required <= keys or keys - required - optional:
            raise RecoveryPlanError(
                f"state projection resource schema differs: {address}"
            )
        identity_keys = {"identity", "identity_schema_version"}
        if keys & identity_keys and not identity_keys <= keys:
            raise RecoveryPlanError(
                f"state projection identity schema differs: {address}"
            )
        metadata = _resource_change_metadata(address)
        if any(
            _canonical_json_bytes(resource.get(name)) != _canonical_json_bytes(expected)
            for name, expected in metadata.items()
        ):
            raise RecoveryPlanError(
                f"state projection resource metadata differs: {address}"
            )
        expected_identity = EXPECTED_STATE_IDENTITIES.get(address)
        values_document = resource.get("values")
        if (
            expected_identity is None
            or resource.get("schema_version") != expected_identity[1]
            or not isinstance(values_document, Mapping)
            or values_document.get("id") != expected_identity[2]
            or not isinstance(resource.get("sensitive_values"), Mapping)
        ):
            raise RecoveryPlanError(
                f"state projection resource identity differs: {address}"
            )
        if "depends_on" in resource and (
            not isinstance(resource["depends_on"], Sequence)
            or isinstance(resource["depends_on"], (str, bytes))
            or not all(isinstance(item, str) for item in resource["depends_on"])
        ):
            raise RecoveryPlanError(
                f"state projection dependencies are invalid: {address}"
            )
        if "identity" in resource and (
            not isinstance(resource["identity"], Mapping)
            or isinstance(resource.get("identity_schema_version"), bool)
            or not isinstance(resource.get("identity_schema_version"), int)
        ):
            raise RecoveryPlanError(f"state projection identity is invalid: {address}")
        output[address] = resource
    if set(output) != EXPECTED_EXISTING_ADDRESSES:
        raise RecoveryPlanError("state projection differs from recovery inventory")
    return output


def _validate_noop_change(
    resource_change: Mapping[str, Any],
    *,
    address: str,
    state_resource: Mapping[str, Any],
) -> None:
    metadata = _resource_change_metadata(address)
    if set(resource_change) != {*metadata, "change"} or any(
        _canonical_json_bytes(resource_change.get(name))
        != _canonical_json_bytes(expected)
        for name, expected in metadata.items()
    ):
        raise RecoveryPlanError(f"existing resource metadata differs: {address}")
    change = resource_change.get("change")
    if not isinstance(change, Mapping):
        raise RecoveryPlanError(f"existing resource action body is invalid: {address}")
    base_keys = {
        "actions",
        "before",
        "after",
        "after_unknown",
        "before_sensitive",
        "after_sensitive",
    }
    identity_keys = {"before_identity", "after_identity"}
    state_has_identity = "identity" in state_resource
    expected_change_keys = base_keys | (identity_keys if state_has_identity else set())
    if set(change) != expected_change_keys:
        raise RecoveryPlanError(f"existing resource action schema differs: {address}")
    if change.get("actions") != ["no-op"]:
        raise RecoveryPlanError(f"existing resource would change: {address}")
    expected_values = state_resource["values"]
    if any(
        _canonical_json_bytes(change.get(name))
        != _canonical_json_bytes(expected_values)
        for name in ("before", "after")
    ):
        raise RecoveryPlanError(
            f"existing resource values differ from protected state: {address}"
        )
    expected_sensitive = state_resource["sensitive_values"]
    if any(
        _canonical_json_bytes(change.get(name))
        != _canonical_json_bytes(expected_sensitive)
        for name in ("before_sensitive", "after_sensitive")
    ):
        raise RecoveryPlanError(
            f"existing resource sensitivity differs from protected state: {address}"
        )
    if change.get("after_unknown") != {}:
        raise RecoveryPlanError(f"existing resource has unknown values: {address}")
    if state_has_identity:
        expected_identity = state_resource["identity"]
        if any(
            _canonical_json_bytes(change.get(name))
            != _canonical_json_bytes(expected_identity)
            for name in identity_keys
        ):
            raise RecoveryPlanError(
                f"existing resource identity differs from protected state: {address}"
            )


def _validate_plan(
    plan: Mapping[str, Any],
    *,
    now: datetime,
    state_projection: Mapping[str, Any],
) -> str:
    if plan.get("format_version") != EXPECTED_FORMAT_VERSION:
        raise RecoveryPlanError("plan JSON format version is not pinned")
    if plan.get("terraform_version") != EXPECTED_TOFU_VERSION:
        raise RecoveryPlanError("plan OpenTofu version is not pinned")
    if plan.get("errored") is not False:
        raise RecoveryPlanError("plan is errored or does not prove success")
    for optional_field in ("applyable", "complete"):
        if optional_field in plan and plan[optional_field] is not True:
            raise RecoveryPlanError(f"plan reports {optional_field}=false")
    if plan.get("resource_drift") not in (None, []):
        raise RecoveryPlanError("plan reports resource drift")
    if plan.get("deferred_changes") not in (None, []):
        raise RecoveryPlanError("plan contains deferred changes")
    if plan.get("action_invocations") not in (None, []):
        raise RecoveryPlanError("plan contains action invocations")

    timestamp = _validate_plan_timestamp(plan, now=now)
    _validate_provider_configuration(plan)
    _validate_variables(plan)
    _validate_checks(plan)

    prior_state = plan.get("prior_state")
    expected_prior_state = {
        "format_version": state_projection.get("format_version"),
        "terraform_version": state_projection.get("terraform_version"),
        "values": state_projection.get("values"),
    }
    if not isinstance(prior_state, Mapping) or _canonical_json_bytes(
        prior_state
    ) != _canonical_json_bytes(expected_prior_state):
        raise RecoveryPlanError(
            "plan prior state differs from the protected state projection"
        )
    state_resources = _projected_state_resources(state_projection)

    changes = plan.get("resource_changes")
    if not isinstance(changes, Sequence) or isinstance(changes, (str, bytes)):
        raise RecoveryPlanError("plan resource changes are missing")
    observed: dict[str, Mapping[str, Any]] = {}
    for resource_change in changes:
        if not isinstance(resource_change, Mapping):
            raise RecoveryPlanError("plan contains an invalid resource change")
        address = resource_change.get("address")
        if not isinstance(address, str):
            raise RecoveryPlanError("plan contains an invalid resource address")
        if address in observed:
            raise RecoveryPlanError("plan contains a duplicate resource change")
        observed[address] = resource_change

    expected_addresses = EXPECTED_EXISTING_ADDRESSES | EXPECTED_CREATE_ADDRESSES
    if set(observed) != set(expected_addresses):
        raise RecoveryPlanError(
            "plan resource address set is not the exact recovery set"
        )
    for address in EXPECTED_EXISTING_ADDRESSES:
        _validate_noop_change(
            observed[address], address=address, state_resource=state_resources[address]
        )
    for address in EXPECTED_CREATE_ADDRESSES:
        if _canonical_json_bytes(observed[address]) != _canonical_json_bytes(
            _expected_create_change(address)
        ):
            raise RecoveryPlanError(
                f"missing resource does not match exact create contract: {address}"
            )
    return timestamp


def _validate_captured_artifacts(
    *,
    saved_plan_bytes: bytes,
    state_bytes: bytes,
    source: SourceSnapshot,
    backend_metadata_sha256: str,
    runtime: RuntimeSnapshot,
    data_dir: Path,
    generation_mode: str,
    now: datetime,
    expected_saved_plan_sha256: str | None = None,
) -> Mapping[str, Any]:
    saved_plan_sha256 = _sha256_bytes(saved_plan_bytes)
    if saved_plan_sha256 in REJECTED_SAVED_PLAN_SHA256S:
        raise RecoveryPlanError("saved plan digest is permanently rejected")
    if (
        expected_saved_plan_sha256 is not None
        and saved_plan_sha256 != expected_saved_plan_sha256
    ):
        raise RecoveryPlanError("saved plan digest does not match reviewed evidence")

    external_state = _load_json_bytes(state_bytes, label="state snapshot")
    lineage, serial, external_inventory = _validate_state(
        external_state, label="state snapshot"
    )
    embedded_state_bytes, embedded_state = _inspect_plan_archive(
        saved_plan_bytes, source=source
    )
    embedded_lineage, embedded_serial, embedded_inventory = _validate_state(
        embedded_state, label="saved plan embedded state"
    )
    if (embedded_lineage, embedded_serial, embedded_inventory) != (
        lineage,
        serial,
        external_inventory,
    ):
        raise RecoveryPlanError(
            "saved plan embedded state does not match the protected snapshot"
        )
    state_semantic_sha256 = _semantic_state_sha256(
        external_state, label="state snapshot"
    )
    if (
        _semantic_state_sha256(embedded_state, label="saved plan embedded state")
        != state_semantic_sha256
    ):
        raise RecoveryPlanError(
            "saved plan embedded state is not semantically identical to the "
            "protected snapshot"
        )

    state_projection_bytes = _show_state(
        state_bytes, tofu=runtime.tofu, data_dir=data_dir
    )
    state_projection = _load_json_bytes(
        state_projection_bytes, label="derived state JSON"
    )
    _projected_state_resources(state_projection)
    plan_json_bytes = _show_saved_plan(
        saved_plan_bytes, tofu=runtime.tofu, data_dir=data_dir
    )
    plan = _load_json_bytes(plan_json_bytes, label="derived plan JSON")
    plan_timestamp = _validate_plan(plan, now=now, state_projection=state_projection)
    return {
        "schema_version": 2,
        "policy_id": "cosmaneura-staging-rehearsal-base-recovery-plan-v2",
        "generation_mode": generation_mode,
        "source_commit": source.commit,
        "source_tree_sha256": source.tree_sha256,
        "provider_lock_sha256": _sha256_bytes(source.lockfile),
        "backend_metadata_sha256": backend_metadata_sha256,
        "backend_workspace": "default",
        "state_lineage": lineage,
        "state_serial": serial,
        "state_json_sha256": _sha256_bytes(state_bytes),
        "state_semantic_sha256": state_semantic_sha256,
        "state_projection_sha256": _sha256_bytes(state_projection_bytes),
        "embedded_state_sha256": _sha256_bytes(embedded_state_bytes),
        "saved_plan_sha256": saved_plan_sha256,
        "derived_plan_json_sha256": _sha256_bytes(plan_json_bytes),
        "plan_timestamp": plan_timestamp,
        "open_tofu_version": EXPECTED_TOFU_VERSION,
        "open_tofu_source_path": str(EXPECTED_TOFU_PATH),
        "open_tofu_sha256": runtime.tofu_sha256,
        "azure_cli_source_path": str(EXPECTED_AZURE_CLI_PATH),
        "azure_cli_sha256": runtime.azure_cli_sha256,
        "azure_cli_version_sha256": runtime.azure_cli_version_sha256,
        "azure_cli_source_closure_sha256": EXPECTED_AZURE_CLI_CLOSURE_SHA256,
        "azure_cli_closure_sha256": runtime.azure_cli_closure_sha256,
        "runtime_snapshot_sha256": runtime.runtime_snapshot_sha256,
        "trusted_runtime_snapshotted": True,
        "validated_nonempty_change_set": True,
        "artifact_policy_complete": True,
        "live_readback_bound": False,
        "apply_authorized": False,
        "full_refresh_generated_here": generation_mode == "validator_full_refresh",
        "existing_resource_count": len(EXPECTED_EXISTING_ADDRESSES),
        "create_count": len(EXPECTED_CREATE_ADDRESSES),
        "update_count": 0,
        "destroy_or_replace_count": 0,
        "pass": True,
    }


def create_and_validate_recovery_plan(
    *, saved_plan: Path, state_json: Path, expected_source_commit: str
) -> Mapping[str, Any]:
    _reject_symlink_components(saved_plan, label="saved plan", allow_missing_leaf=True)
    if saved_plan.exists():
        raise RecoveryPlanError("saved plan output already exists")
    _require_protected_directory(saved_plan.parent, label="saved plan directory")
    state_bytes = _read_protected_file(
        state_json, label="state snapshot", required_mode=0o600
    )
    source_before = _source_snapshot(expected_source_commit)
    with tempfile.TemporaryDirectory(
        prefix=".cosmaneura-tofu-data-", dir=saved_plan.parent
    ) as temporary_data_dir:
        data_dir = Path(temporary_data_dir)
        runtime = _snapshot_and_verify_runtime(data_dir=data_dir)
        _initialize_tofu(tofu=runtime.tofu, data_dir=data_dir)
        backend_before = _backend_snapshot(tofu=runtime.tofu, data_dir=data_dir)
        saved_plan_bytes = _generate_saved_plan(
            tofu=runtime.tofu, output_parent=saved_plan.parent, data_dir=data_dir
        )
        receipt = _validate_captured_artifacts(
            saved_plan_bytes=saved_plan_bytes,
            state_bytes=state_bytes,
            source=source_before,
            backend_metadata_sha256=backend_before,
            runtime=runtime,
            data_dir=data_dir,
            generation_mode="validator_full_refresh",
            now=datetime.now(UTC),
        )
        if _backend_snapshot(tofu=runtime.tofu, data_dir=data_dir) != backend_before:
            raise RecoveryPlanError(
                "backend metadata changed while the plan was generated"
            )
    if _source_snapshot(expected_source_commit) != source_before:
        raise RecoveryPlanError("recovery source changed while the plan was generated")
    _write_protected_file(saved_plan, saved_plan_bytes, label="saved plan")
    if (
        _sha256_bytes(
            _read_protected_file(saved_plan, label="saved plan", required_mode=0o400)
        )
        != receipt["saved_plan_sha256"]
    ):
        raise RecoveryPlanError("saved plan output does not match validated bytes")
    return receipt


def revalidate_recovery_plan(
    *,
    saved_plan: Path,
    state_json: Path,
    execution_dir: Path,
    expected_source_commit: str,
    expected_saved_plan_sha256: str,
) -> Mapping[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_saved_plan_sha256) is None:
        raise RecoveryPlanError("expected saved-plan digest is invalid")
    if expected_saved_plan_sha256 in REJECTED_SAVED_PLAN_SHA256S:
        raise RecoveryPlanError("saved plan digest is permanently rejected")
    saved_plan_bytes = _read_protected_file(
        saved_plan, label="saved plan", required_mode=0o400
    )
    state_bytes = _read_protected_file(
        state_json, label="state snapshot", required_mode=0o600
    )
    if execution_dir.parent != saved_plan.parent:
        raise RecoveryPlanError("execution directory must share the saved-plan parent")
    _create_protected_directory(execution_dir, label="execution directory")
    source_before = _source_snapshot(expected_source_commit)
    runtime = _snapshot_and_verify_runtime(data_dir=execution_dir)
    _initialize_tofu(tofu=runtime.tofu, data_dir=execution_dir)
    backend_before = _backend_snapshot(tofu=runtime.tofu, data_dir=execution_dir)
    receipt = dict(
        _validate_captured_artifacts(
            saved_plan_bytes=saved_plan_bytes,
            state_bytes=state_bytes,
            source=source_before,
            backend_metadata_sha256=backend_before,
            runtime=runtime,
            data_dir=execution_dir,
            generation_mode="digest_bound_revalidation",
            now=datetime.now(UTC),
            expected_saved_plan_sha256=expected_saved_plan_sha256,
        )
    )
    if _backend_snapshot(tofu=runtime.tofu, data_dir=execution_dir) != backend_before:
        raise RecoveryPlanError("backend metadata changed during plan revalidation")
    if _source_snapshot(expected_source_commit) != source_before:
        raise RecoveryPlanError("recovery source changed during plan revalidation")
    receipt.update(
        execution_data_dir=str(execution_dir),
        execution_provider_tree_sha256=_provider_tree_sha256(execution_dir),
        open_tofu_path=str(runtime.tofu),
    )
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("create", "verify"))
    parser.add_argument("--saved-plan", required=True, type=Path)
    parser.add_argument("--state-json", required=True, type=Path)
    parser.add_argument("--execution-dir", type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-saved-plan-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "create":
            if args.expected_saved_plan_sha256 is not None or args.execution_dir:
                raise RecoveryPlanError(
                    "create mode does not accept a plan digest or execution directory"
                )
            receipt = create_and_validate_recovery_plan(
                saved_plan=args.saved_plan,
                state_json=args.state_json,
                expected_source_commit=args.expected_source_commit,
            )
        else:
            if args.expected_saved_plan_sha256 is None or args.execution_dir is None:
                raise RecoveryPlanError(
                    "verify mode requires the reviewed plan digest and new execution directory"
                )
            receipt = revalidate_recovery_plan(
                saved_plan=args.saved_plan,
                state_json=args.state_json,
                execution_dir=args.execution_dir,
                expected_source_commit=args.expected_source_commit,
                expected_saved_plan_sha256=args.expected_saved_plan_sha256,
            )
    except RecoveryPlanError as exc:
        print(f"recovery_plan_refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
