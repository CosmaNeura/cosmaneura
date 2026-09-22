output "proposal" {
  description = "Planned network only, not observed reachability, isolation, runner setup or publication authority."
  value = {
    enabled                       = local.active
    location                      = local.location
    vnet_cidr                     = "10.77.0.0/24"
    runner_cidr                   = local.subnets.runner.cidr
    registry_endpoint_cidr        = local.subnets.registry.cidr
    reviewed_https_domains        = sort(tolist(var.reviewed_https_domains))
    firewall_sku                  = "Basic"
    existing_registry_managed     = false
    publisher_identity_managed    = false
    push_grants_managed           = false
    github_runner_managed         = false
    github_configuration_managed  = false
    private_reachability_proven   = false
    publisher_containment_proven  = false
    images_published              = false
    runtime_acceptance            = false
    activation_authorized         = false
    network_settings_resource_id  = try(azapi_resource.github_network[0].id, null)
    organization_binding_observed = false
  }
}
