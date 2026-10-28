locals {
  session_prep_production_heartbeat_controlled_env_names = toset([
    "SESSION_PREP_PRODUCTION_HEARTBEAT_ENABLED",
  ])
  session_prep_production_heartbeat_generic_worker_env_names = setunion(
    var.python_public_vars,
    var.python_billing_public_vars,
    var.python_secret_vars,
    var.python_billing_secret_vars,
  )
  session_prep_production_heartbeat_env_name_collisions = setintersection(
    local.session_prep_production_heartbeat_controlled_env_names,
    local.session_prep_production_heartbeat_generic_worker_env_names,
  )
}
