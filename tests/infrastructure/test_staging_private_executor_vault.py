"""Empty-vault source boundaries; not a substitute for live provider readback."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "infra-staging-rehearsal"


def test_vault_source_contains_only_the_four_gated_foundation_resources() -> None:
    source = (ROOT / "vault.tf").read_text(encoding="utf-8")
    resources = re.findall(r'^resource "([^"]+)" "([^"]+)"', source, re.MULTILINE)
    assert len(resources) == 4
    assert set(resources) == {
        ("azapi_resource", "private_executor_vault"),
        ("azurerm_private_endpoint", "private_executor_vault"),
        ("azurerm_private_dns_zone", "private_executor_vault"),
        ("azurerm_private_dns_zone_virtual_network_link", "private_executor_vault"),
    }
    for resource_type, name in resources:
        match = re.search(
            rf'^resource "{resource_type}" "{name}" \{{(?P<body>.*?)^\}}',
            source,
            re.MULTILINE | re.DOTALL,
        )
        assert match is not None
        assert re.search(
            r"^\s*count\s*=\s*local\.private_executor_vault_enabled\s*\?\s*1\s*:\s*0$",
            match["body"],
            re.MULTILINE,
        )


def _assert_no_vault_auxiliaries(sources: dict[str, str]) -> None:
    vault_types = []
    roles = []
    for filename, source in sources.items():
        assert not re.search(
            r'^(?:resource|data) "azurerm_key_vault[^" ]*"', source, re.MULTILINE
        )
        vault_types.extend(
            (filename, resource_type)
            for resource_type in re.findall(
                r'^\s*type\s*=\s*"(Microsoft\.KeyVault/[^\"]+)"',
                source,
                re.MULTILINE,
            )
        )
        for kind, resource_type, name, body in re.findall(
            r'^(resource|data) "([^\"]+)" "([^\"]+)" \{(.*?)^\}',
            source,
            re.MULTILINE | re.DOTALL,
        ):
            vault_reference = (
                "private_executor_vault" in body or "Microsoft.KeyVault/" in body
            )
            if resource_type == "azurerm_role_assignment":
                roles.append(name)
                assert not vault_reference
            if resource_type.startswith("azapi_") and vault_reference:
                assert (filename, kind, resource_type, name) == (
                    "vault.tf",
                    "resource",
                    "azapi_resource",
                    "private_executor_vault",
                )
    assert vault_types == [("vault.tf", "Microsoft.KeyVault/vaults@2023-07-01")]
    # No new grant in any file, even with a literal or computed scope. Existing
    # non-vault grants remain in their separately gated source contracts.
    assert len(roles) == 8
    assert set(roles) == {
        "control_reader",
        "source_reader",
        "target_provisioner",
        "target_cleanup",
        "marker_read",
        "marker_provision_cleanup",
        "marker_create",
        "private_executor_acrpull",
    }


def test_key_vault_is_arm_only_with_no_secret_certificate_key_or_grant() -> None:
    _assert_no_vault_auxiliaries(
        {path.name: path.read_text(encoding="utf-8") for path in ROOT.glob("*.tf")}
    )
    vault = (ROOT / "vault.tf").read_text(encoding="utf-8")
    assert 'resource "azurerm_role_assignment"' not in vault
    assert 'resource "azapi_resource_action"' not in vault
    assert re.search(r'^\s*createMode\s*=\s*"default"$', vault, re.MULTILINE)
    assert re.search(r"^\s*prevent_destroy\s*=\s*true$", vault, re.MULTILINE)
    assert re.search(r"^\s*schema_validation_enabled\s*=\s*true$", vault, re.MULTILINE)
    assert re.search(
        r'^\s*response_export_values\s*=\s*\["properties\.vaultUri"\]$',
        vault,
        re.MULTILINE,
    )


@pytest.mark.parametrize(
    "extra",
    [
        'data "azurerm_key_vault" "later" {\n}\n',
        'data "azurerm_key_vault_secret" "later" {\n}\n',
        'resource "azurerm_key_vault_secret" "later" {\n}\n',
        'resource "azurerm_key_vault_certificate_contacts" "later" {\n}\n',
        'resource "azapi_resource" "secret" {\n type = "Microsoft.KeyVault/vaults/secrets@2023-07-01"\n}\n',
        'data "azapi_resource" "later" {\n type = "Microsoft.KeyVault/vaults@2023-07-01"\n}\n',
        'resource "azapi_resource_action" "later" {\n type = local.vault_type\n resource_id = azapi_resource.private_executor_vault[0].id\n}\n',
        'resource "azapi_data_plane_resource" "later" {\n type = "Microsoft.KeyVault/vaults/keys@7.4"\n}\n',
        'resource "azurerm_role_assignment" "later" {\n scope = azapi_resource.private_executor_vault[0].id\n}\n',
        'resource "azurerm_role_assignment" "later" {\n scope = var.indirect_scope\n}\n',
    ],
)
def test_future_auxiliary_surface_in_another_file_is_rejected(extra: str) -> None:
    sources = {
        path.name: path.read_text(encoding="utf-8") for path in ROOT.glob("*.tf")
    }
    sources["later.tf"] = extra
    with pytest.raises(AssertionError):
        _assert_no_vault_auxiliaries(sources)


def test_existing_grant_cannot_be_redirected_to_vault() -> None:
    sources = {
        path.name: path.read_text(encoding="utf-8") for path in ROOT.glob("*.tf")
    }
    sources["authorization.tf"], replacements = re.subn(
        r'(resource "azurerm_role_assignment" "control_reader" \{.*?^\s*scope\s*=\s*)local\.subscription_scope$',
        r"\g<1>azapi_resource.private_executor_vault[0].id",
        sources["authorization.tf"],
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert replacements == 1
    with pytest.raises(AssertionError):
        _assert_no_vault_auxiliaries(sources)


def test_vault_creation_plan_and_purge_approval_default_off() -> None:
    source = (ROOT / "variables.tf").read_text(encoding="utf-8")
    for name in (
        "enable_private_executor_vault",
        "private_executor_vault_plan_approved",
        "private_executor_vault_purge_protection_approved",
    ):
        match = re.search(
            rf'variable "{name}" \{{(?P<body>.*?)\n\}}', source, re.DOTALL
        )
        assert match is not None
        assert re.search(r"^\s*default\s*=\s*false$", match["body"], re.MULTILINE)
