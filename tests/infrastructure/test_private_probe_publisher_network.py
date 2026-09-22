"""Static scope/CI guards for the dormant network proposal; no Azure calls."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "infra-staging-rehearsal/modules/private-probe-publisher-network"


def test_resource_inventory_excludes_publishers_identities_and_existing_registry() -> (
    None
):
    source = "\n".join(path.read_text() for path in sorted(MODULE.glob("*.tf")))
    resources = re.findall(r'^resource "([^"]+)" "([^"]+)" [{]', source, re.MULTILINE)
    assert len(resources) == 20
    assert set(resources) == {
        ("terraform_data", "guard"),
        ("azurerm_resource_group", "publisher"),
        ("azurerm_virtual_network", "publisher"),
        ("azurerm_subnet", "firewall"),
        ("azurerm_subnet", "management"),
        ("azurerm_subnet", "runner"),
        ("azurerm_subnet", "registry"),
        ("azurerm_network_security_group", "runner"),
        ("azurerm_network_security_rule", "runner"),
        ("azurerm_subnet_network_security_group_association", "runner"),
        ("azurerm_public_ip", "firewall"),
        ("azurerm_firewall_policy", "publisher"),
        ("azurerm_firewall_policy_rule_collection_group", "egress"),
        ("azurerm_firewall", "publisher"),
        ("azurerm_route_table", "runner"),
        ("azurerm_subnet_route_table_association", "runner"),
        ("azurerm_private_dns_zone", "registry"),
        ("azurerm_private_dns_zone_virtual_network_link", "registry"),
        ("azurerm_private_endpoint", "registry"),
        ("azapi_resource", "github_network"),
    }


@pytest.mark.parametrize("keyword", ["provider", "data", "module", "provisioner"])
def test_module_has_no_ambient_or_executable_integration(keyword: str) -> None:
    for path in MODULE.glob("*.tf"):
        assert not re.search(rf'^\s*{keyword}\s+"', path.read_text(), re.MULTILINE)


def test_existing_root_does_not_instantiate_publisher_module() -> None:
    for path in (ROOT / "infra-staging-rehearsal").glob("*.tf"):
        assert "private-probe-publisher-network" not in path.read_text()


def test_ci_covers_each_new_mock_test_without_cloud_authority() -> None:
    workflow = (ROOT / ".github/workflows/infra_plan.yml").read_text()
    assert "'infra-staging-rehearsal/**'" in workflow
    assert "tofu init -backend=false -input=false -lockfile=readonly" in workflow
    assert "tofu test -test-directory=tests -no-color" in workflow
    for name in (
        "private_probe_publisher_network.tftest.hcl",
        "private_probe_publisher_network_enabled.tftest.hcl",
        "private_probe_publisher_network_inputs.tftest.hcl",
    ):
        source = (ROOT / "infra-staging-rehearsal/tests" / name).read_text()
        assert 'mock_provider "azapi"' in source
        assert 'mock_provider "azurerm"' in source
        assert "command = apply" not in source
        assert 'source = "./modules/private-probe-publisher-network"' in source


def test_network_settings_is_last_in_dependency_graph() -> None:
    source = (MODULE / "main.tf").read_text()
    settings = source.split('resource "azapi_resource" "github_network" {', 1)[1]
    for dependency in (
        "azurerm_subnet_network_security_group_association.runner",
        "azurerm_subnet_route_table_association.runner",
        "azurerm_firewall_policy_rule_collection_group.egress",
        "azurerm_private_endpoint.registry",
    ):
        assert dependency in settings
