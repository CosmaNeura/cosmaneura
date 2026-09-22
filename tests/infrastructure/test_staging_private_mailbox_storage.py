"""Private mailbox source invariants; no live authorization or storage proof."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "infra-staging-rehearsal"


def _resource(source: str, resource_type: str, name: str) -> str:
    match = re.search(
        rf'^resource "{resource_type}" "{name}" \{{(?P<body>.*?)^\}}',
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    return match["body"]


def test_mailbox_storage_has_only_four_exact_gated_resource_declarations() -> None:
    source = (ROOT / "launch-mailbox.tf").read_text(encoding="utf-8")
    resources = re.findall(r'^resource "([^"]+)" "([^"]+)"', source, re.MULTILINE)
    assert len(resources) == 4
    assert set(resources) == {
        ("azapi_resource", "private_launch_mailbox_account"),
        ("azapi_update_resource", "private_launch_mailbox_blob_versioning"),
        ("azapi_resource", "private_launch_mailbox_container"),
        ("azurerm_private_endpoint", "private_launch_mailbox"),
    }
    for resource_type, name in resources:
        body = _resource(source, resource_type, name)
        if name == "private_launch_mailbox_container":
            assert re.search(
                r"^\s*for_each\s*=\s*local\.private_launch_mailbox_storage_enabled"
                r"\s*\?\s*local\.private_launch_mailbox_containers\s*:\s*\{\}$",
                body,
                re.MULTILINE,
            )
        else:
            assert re.search(
                r"^\s*count\s*=\s*local\.private_launch_mailbox_storage_enabled"
                r"\s*\?\s*1\s*:\s*0$",
                body,
                re.MULTILINE,
            )


def test_mailbox_management_stays_arm_only_without_credentials_or_actions() -> None:
    source = (ROOT / "launch-mailbox.tf").read_text(encoding="utf-8")
    assert not re.search(r'^data\s+"', source, re.MULTILINE)
    assert not re.search(
        r"^\s*(identity|provisioner|connection)\s*\{", source, re.MULTILINE
    )
    for forbidden in (
        'resource "azurerm_storage_account"',
        'resource "azurerm_storage_blob"',
        'resource "azurerm_storage_container"',
        'resource "azurerm_role_assignment"',
        'resource "azapi_resource_action"',
        'resource "azapi_data_plane_resource"',
        'resource "azurerm_storage_container_immutability_policy"',
        'resource "azurerm_management_lock"',
        "primary_access_key",
        "connection_string",
    ):
        assert forbidden not in source
    account = _resource(source, "azapi_resource", "private_launch_mailbox_account")
    assert re.search(r"^\s*prevent_destroy\s*=\s*true$", account, re.MULTILINE)
    assert re.search(
        r"^\s*schema_validation_enabled\s*=\s*true$", account, re.MULTILINE
    )
    assert re.search(
        r'^\s*response_export_values\s*=\s*\["properties\.primaryEndpoints\.blob"\]$',
        account,
        re.MULTILINE,
    )


def test_private_containers_depend_on_exact_account_blob_versioning() -> None:
    source = (ROOT / "launch-mailbox.tf").read_text(encoding="utf-8")
    container = _resource(source, "azapi_resource", "private_launch_mailbox_container")
    versioning = _resource(
        source, "azapi_update_resource", "private_launch_mailbox_blob_versioning"
    )
    assert re.search(r"^\s*name\s*=\s*each\.value$", container, re.MULTILINE)
    assert re.search(r"^\s*prevent_destroy\s*=\s*true$", container, re.MULTILINE)
    assert re.search(
        r"^\s*depends_on\s*=\s*\[azapi_update_resource\.private_launch_mailbox_blob_versioning\]$",
        container,
        re.MULTILINE,
    )
    exact_child = (
        '"${azapi_resource.private_launch_mailbox_account[0].id}/blobServices/default"'
    )
    assert re.search(
        r"^\s*parent_id\s*=\s*" + re.escape(exact_child) + "$", container, re.MULTILINE
    )
    assert re.search(
        r"^\s*resource_id\s*=\s*" + re.escape(exact_child) + "$",
        versioning,
        re.MULTILINE,
    )
    for body in (container, versioning):
        assert re.search(r"^\s*response_export_values\s*=\s*\[\]$", body, re.MULTILINE)


def test_mailbox_creation_inputs_default_off_and_are_independent() -> None:
    variables = (ROOT / "variables.tf").read_text(encoding="utf-8")
    for flag in (
        "enable_private_launch_mailbox_storage",
        "private_launch_mailbox_storage_plan_approved",
    ):
        match = re.search(
            rf'variable "{flag}" \{{(?P<body>.*?)\n\}}', variables, re.DOTALL
        )
        assert match is not None
        assert re.search(r"^\s*default\s*=\s*false$", match["body"], re.MULTILINE)
    source = (ROOT / "launch-mailbox.tf").read_text(encoding="utf-8")
    assert re.search(
        r"private_launch_mailbox_storage_enabled\s*=\s*\(\s*"
        r"var\.deploy_staging_restore_control_plane\s*&&\s*"
        r"var\.enable_private_executor_network_foundation\s*&&\s*"
        r"var\.enable_private_launch_mailbox_storage\s*\)",
        source,
    )


def test_mailbox_endpoint_uses_existing_blob_dns_and_service_subnet() -> None:
    source = (ROOT / "launch-mailbox.tf").read_text(encoding="utf-8")
    endpoint = _resource(source, "azurerm_private_endpoint", "private_launch_mailbox")
    assert re.search(
        r"^\s*subnet_id\s*=\s*azurerm_subnet\.private_executor_service_endpoints\[0\]\.id$",
        endpoint,
        re.MULTILINE,
    )
    assert re.search(
        r"^\s*private_dns_zone_ids\s*=\s*\[azurerm_private_dns_zone\.marker_blob\[0\]\.id\]$",
        endpoint,
        re.MULTILINE,
    )
    assert re.search(
        r"^\s*depends_on\s*=\s*\[azurerm_private_dns_zone_virtual_network_link\.marker_blob\]$",
        endpoint,
        re.MULTILINE,
    )
    assert 'resource "azurerm_private_dns_zone"' not in source
    assert 'resource "azurerm_private_dns_zone_virtual_network_link"' not in source
