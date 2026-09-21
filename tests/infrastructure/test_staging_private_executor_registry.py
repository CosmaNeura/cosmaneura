"""Source invariants complement mocked plans; neither proves live Azure state."""

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


def test_registry_source_has_only_its_exact_reviewed_resources() -> None:
    source = (ROOT / "registry.tf").read_text(encoding="utf-8")
    resources = re.findall(r'^resource "([^"]+)" "([^"]+)"', source, re.MULTILINE)
    assert len(resources) == 5
    assert set(resources) == {
        ("azurerm_container_registry", "private_executor"),
        ("azurerm_private_dns_zone", "private_executor_registry"),
        ("azurerm_private_dns_zone_virtual_network_link", "private_executor_registry"),
        ("azurerm_private_endpoint", "private_executor_registry"),
        ("azurerm_role_assignment", "private_executor_acrpull"),
    }
    for resource_type, name in resources:
        body = _resource(source, resource_type, name)
        if resource_type == "azurerm_role_assignment":
            assert re.search(
                r"^\s*for_each\s*=\s*local\.private_executor_registry_enabled"
                r"\s*\?\s*local\.restore_job_identities\s*:\s*\{\}\s*$",
                body,
                re.MULTILINE,
            )
        else:
            assert re.search(
                r"^\s*count\s*=\s*local\.private_executor_registry_enabled"
                r"\s*\?\s*1\s*:\s*0\s*$",
                body,
                re.MULTILINE,
            )


def test_each_pull_assignment_selects_its_own_job_not_a_shared_principal() -> None:
    source = (ROOT / "registry.tf").read_text(encoding="utf-8")
    body = _resource(source, "azurerm_role_assignment", "private_executor_acrpull")
    # The provider mock is resource-wide. This independent source invariant
    # catches accidentally giving both roles to provision (or cleanup).
    assert re.search(
        r"^\s*principal_id\s*=\s*azurerm_user_assigned_identity\.job\[each\.key\]\.principal_id$",
        body,
        re.MULTILINE,
    )
    assert re.search(
        r"^\s*scope\s*=\s*azurerm_container_registry\.private_executor\[0\]\.id$",
        body,
        re.MULTILINE,
    )
    assert re.search(
        r"^\s*role_definition_id\s*=\s*local\.acrpull_role_definition_id$",
        body,
        re.MULTILINE,
    )
    assert '/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"' in source


def test_registry_remains_destruction_guarded_without_identity_or_credentials() -> None:
    source = (ROOT / "registry.tf").read_text(encoding="utf-8")
    registry = _resource(source, "azurerm_container_registry", "private_executor")
    assert re.search(r"^\s*prevent_destroy\s*=\s*true$", registry, re.MULTILINE)
    assert not re.search(r"^\s*identity\s*\{", registry, re.MULTILINE)
    assert not re.search(
        r"^\s*(admin_username|admin_password)\s*=", registry, re.MULTILINE
    )
    for flag in (
        "admin_enabled",
        "anonymous_pull_enabled",
        "public_network_access_enabled",
        "export_policy_enabled",
    ):
        assert re.search(rf"^\s*{flag}\s*=\s*false$", registry, re.MULTILINE)
