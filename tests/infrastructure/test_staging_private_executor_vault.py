"""Empty-vault source boundaries; not a substitute for live provider readback."""

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "infra-staging-rehearsal"

# Exact declaration-text change detectors, not a general HCL expression or
# transitive-local evaluator. Deliberate future changes need reviewed snapshots
# plus semantic provider-mocked plans and fresh live authorization evidence.
EXPECTED_DECLARATIONS = {
    "launch-mailbox.tf:resource:azapi_resource:private_launch_mailbox_account": "2b5db9047d753b76f96badd97852d9d794030acc3c71e30ec3e96e99f98cf5e6",
    "launch-mailbox.tf:resource:azapi_update_resource:private_launch_mailbox_blob_versioning": "0fb0fc19513a1058eb65ff63145dcfa2a750ec414ebf770277f9d91f2b8d7d6d",
    "launch-mailbox.tf:resource:azapi_resource:private_launch_mailbox_container": "cf73f71f059295b51e50df8842ac80c95d50e34a353456e3c221fdf117caa9e6",
    "evidence-storage.tf:resource:azapi_resource:private_evidence_account": "78037b662f7f8a70fed9a848115a5945aded533fbc21e4fe9cc79b208ef533b2",
    "evidence-storage.tf:resource:azapi_update_resource:private_evidence_blob_versioning": "34ac2dc117a1e2d5366ba2b06be53adec5d11a250f02eef73f4a60213f86e0c3",
    "evidence-storage.tf:resource:azapi_resource:private_evidence_container": "61551c8cf4e112a1ee50807e1704931117b2c8c93bbaff146091cad3def3acdf",
    "authorization.tf:resource:azurerm_role_assignment:control_reader": "83072d5c786a64746c661d6dc4639ae8b02eafb77b1cf0b1cc551c909a6e2c61",
    "authorization.tf:resource:azurerm_role_assignment:marker_create": "10f69986ff22161b50664887e20b10a6f9e93986b04dd6f0d5fc051cb33a9a63",
    "authorization.tf:resource:azurerm_role_assignment:marker_provision_cleanup": "59813de17a7252978f7d1c9a968a8e77a345950aafe43e40742c15ae6d32dab0",
    "authorization.tf:resource:azurerm_role_assignment:marker_read": "c6ba333584214cc6a90bed31e8fcaaeb7753cd98320c287b8b4169678ebbb80a",
    "authorization.tf:resource:azurerm_role_assignment:source_reader": "8dceac28e66b86a0e9c229add997ed090d921a36265092636fec4c2be01d88cf",
    "authorization.tf:resource:azurerm_role_assignment:target_cleanup": "2b2bbc2151516df563419eb6aea4d887f331928b8c48ddd7ea69c60a78373994",
    "authorization.tf:resource:azurerm_role_assignment:target_provisioner": "6ceb0c437a62b033c033a2ddc53ee5132b2265d7226bb86d4521ba091e66dfa3",
    "registry.tf:resource:azurerm_role_assignment:private_executor_acrpull": "58568bb43efd93345c1810ea3e11a162c124df4447d0866962321f87e7620aa0",
    "storage.tf:resource:azapi_resource:marker_container": "7548e311247c950db8c5160a99852e72994c55fdd116adb7204d28c750e6f465",
    "storage.tf:resource:azapi_resource_action:marker_legal_hold": "165dbf73a72f55df8f1013f7e115ee9e2caa67c02902d2d12c63aaa541aa56c1",
    "vault.tf:resource:azapi_resource:private_executor_vault": "e28b05c7bff70e02fc5d905fb71f53303cece5e4a7c222671f0f8e3a089ca803",
}


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
    declarations = {}
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
        for match in re.finditer(
            r'^(resource|data) "([^\"]+)" "([^\"]+)" \{(.*?)^\}',
            source,
            re.MULTILINE | re.DOTALL,
        ):
            kind, resource_type, name, body = match.groups()
            if resource_type == "azurerm_role_assignment" or resource_type.startswith(
                "azapi_"
            ):
                key = f"{filename}:{kind}:{resource_type}:{name}"
                assert key not in declarations
                declarations[key] = hashlib.sha256(match[0].encode()).hexdigest()
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
    assert declarations == EXPECTED_DECLARATIONS
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
        'locals {\n hidden_type = "Microsoft.KeyVault/vaults@2023-07-01"\n hidden_target = azapi_resource.private_executor_vault[0].id\n}\nresource "azapi_resource_action" "hidden" {\n type = local.hidden_type\n resource_id = local.hidden_target\n}\n',
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


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("scope", "local.redirected_scope"),
        (
            "role_definition_id",
            '"/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6"',
        ),
    ],
)
def test_existing_role_body_cannot_hide_scope_or_role_changes(
    field: str, replacement: str
) -> None:
    sources = {
        path.name: path.read_text(encoding="utf-8") for path in ROOT.glob("*.tf")
    }
    pattern = (
        r'(resource "azurerm_role_assignment" "control_reader" \{.*?^\s*'
        + field
        + r"\s*=\s*)([^\n]+)"
    )
    sources["authorization.tf"], count = re.subn(
        pattern,
        lambda match: match[1] + replacement,
        sources["authorization.tf"],
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert count == 1
    sources["alias.tf"] = (
        "locals {\n redirected_scope = azapi_resource.private_executor_vault[0].id\n}\n"
    )
    with pytest.raises(AssertionError):
        _assert_no_vault_auxiliaries(sources)


def test_existing_azapi_action_cannot_hide_a_retargeted_body() -> None:
    sources = {
        path.name: path.read_text(encoding="utf-8") for path in ROOT.glob("*.tf")
    }
    sources["storage.tf"], count = re.subn(
        r'^resource "azapi_resource_action" "marker_legal_hold" \{.*?^\}',
        'resource "azapi_resource_action" "marker_legal_hold" {\n'
        " type = local.hidden_type\n resource_id = local.hidden_target\n"
        ' action = "metadata"\n method = "GET"\n}',
        sources["storage.tf"],
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert count == 1
    sources["alias.tf"] = (
        'locals {\n hidden_type = "Microsoft.KeyVault/vaults@2023-07-01"\n'
        " hidden_target = azapi_resource.private_executor_vault[0].id\n}\n"
    )
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
