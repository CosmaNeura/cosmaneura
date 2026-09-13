import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NETWORK_ROOT = ROOT / "infra-staging-rehearsal"


def _terraform_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(NETWORK_ROOT.glob("*.tf"))
    )


def test_private_executor_network_has_exact_default_off_contract() -> None:
    source = _terraform_source()
    variables = (NETWORK_ROOT / "variables.tf").read_text(encoding="utf-8")

    for name in (
        "enable_private_executor_network_foundation",
        "private_executor_network_plan_approved",
    ):
        match = re.search(
            rf'variable "{name}" \{{(?P<body>.*?)\n\}}', variables, re.DOTALL
        )
        assert match is not None
        assert re.search(r"^\s*default\s*=\s*false\s*$", match["body"], re.MULTILINE)

    assert '"10.76.0.0/24"' in source
    assert '"10.76.0.0/27"' in source
    assert '"10.76.0.32/27"' in source
    assert '"10.76.0.64/27"' in source
    assert 'name = "Microsoft.App/environments"' in source


def test_unimplemented_connectivity_and_compute_remain_absent() -> None:
    source = _terraform_source()
    forbidden_resource_types = (
        "azurerm_container_app_environment",
        "azurerm_container_app_job",
        "azurerm_key_vault",
        "azurerm_nat_gateway",
        "azurerm_network_security_group",
        "azurerm_public_ip",
        "azurerm_route_table",
        "azurerm_virtual_network_peering",
    )

    for resource_type in forbidden_resource_types:
        assert f'resource "{resource_type}"' not in source

    # A separately gated registry rung is now present. It must never move into
    # the network-only source, and no second file may introduce another ACR.
    registry_sources = {
        path.name
        for path in NETWORK_ROOT.glob("*.tf")
        if re.search(
            r'^resource "azurerm_container_registry"',
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    }
    assert registry_sources == {"registry.tf"}


def test_service_endpoint_subnet_reserves_endpoints_without_creating_them() -> None:
    network = (NETWORK_ROOT / "network.tf").read_text(encoding="utf-8")

    assert 'resource "azurerm_subnet" "private_executor_service_endpoints"' in network
    assert 'private_endpoint_network_policies             = "Disabled"' in network
    assert "private_link_service_network_policies_enabled = true" in network
    assert "default_outbound_access_enabled               = false" in network
    assert network.count('resource "azurerm_private_endpoint"') == 0
