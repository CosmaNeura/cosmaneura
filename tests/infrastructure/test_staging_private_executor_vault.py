"""Empty-vault source boundaries; not a substitute for live provider readback."""

import re
from pathlib import Path

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


def test_key_vault_is_arm_only_with_no_secret_certificate_key_or_grant() -> None:
    vault_types = []
    for path in ROOT.glob("*.tf"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(
            r'^resource "azurerm_key_vault[^" ]*"', source, re.MULTILINE
        )
        vault_types.extend(
            (path.name, resource_type)
            for resource_type in re.findall(
                r'^\s*type\s*=\s*"(Microsoft\.KeyVault/[^\"]+)"',
                source,
                re.MULTILINE,
            )
        )
    assert vault_types == [("vault.tf", "Microsoft.KeyVault/vaults@2023-07-01")]
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
