#!/usr/bin/env bash

# Description: this insane mess deploys already-built containers to production
#
#   - By default, Azure container apps do NOT run their health checks and fail loudly. This is the main goal of this script.
#     containers are deployed and then health checks for the containers are run.
#
#   - The version numbers that are auto-gen'd are just random hashes which makes it hard to determine if your container
#     deployed properly. This is why we generate our own version numbers here.
#
#   - Containers are not rolled back if one of them fails. We assume that if one fails, they will all fail, which isn't a great
#     assumption.

GIT_SHA="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../scripts/deploy_revision_suffix.sh"

# TODO(mbianco) these should really be pulled from the ENV or somewhere more stable
# Define variables
RESOURCE_GROUP="cosmaneura-prod"
MIGRATION_JOB="cosmaneura-prod-migrate"
WORKER_SCHEDULER_CONTAINERS="cosmaneura-prod-worker cosmaneura-prod-scheduler"
WORKER_CONTAINER="cosmaneura-prod-worker"
API_CONTAINER="cosmaneura-prod-app"
JMON_CONTAINER="cosmaneura-prod-app-jmon"
PRODUCTION_IMAGE="cosmaneuraprodregistry.azurecr.io/cosmaneura/cosmaneura"

# Resolve protected configuration before invoking git, Docker, Azure, or any
# other provider-facing subprocess. Keep the resolved JSON in this shell only,
# then drop the ambient capabilities immediately. ENV_JSON is deliberately not
# exported, so child processes receive only values passed to them explicitly.
if ! ENV_JSON=$(cd "$SCRIPT_DIR/.." && just direnv_export .env.production.backend); then
	echo "Failed to load production deployment configuration."
	exit 1
fi
if ! printf '%s' "$ENV_JSON" |
	python "$SCRIPT_DIR/../scripts/superadmin_capability_config_preflight.py" \
		--environment production; then
	echo "Production capability identity configuration is not deployable."
	exit 1
fi
unset OP_SERVICE_ACCOUNT_TOKEN RECALL_WEBHOOK_SECRET

# Check for Git SHA input and validate it exists in repo
if [[ -z "$GIT_SHA" ]]; then
	echo "Error: Please provide a Git SHA as an argument."
	exit 1
fi

if ! git rev-parse --quiet --verify "$GIT_SHA^{commit}" >/dev/null; then
	echo "Error: $GIT_SHA is not a valid commit in this repository."
	exit 1
fi

# Convert potential short SHA to full SHA
GIT_SHA=$(git rev-parse "$GIT_SHA")
PRODUCTION_TAG="$PRODUCTION_IMAGE:$GIT_SHA"

if ! REVISION_STRING=$(generate_deploy_revision_suffix "$GIT_SHA"); then
	exit 1
fi
if ! validate_deploy_revision_suffix "$REVISION_STRING" \
	$WORKER_SCHEDULER_CONTAINERS "$API_CONTAINER" "$JMON_CONTAINER"; then
	exit 1
fi

echo "Revision string: $REVISION_STRING"

echo "Azure Acccount Information:"
az account show

echo "All Available Containers:"
az containerapp list --query "[].name"

if ! docker manifest inspect "$PRODUCTION_TAG" >/dev/null 2>&1; then
	echo "Error: Image $PRODUCTION_TAG does not exist"
	exit 1
fi

DATABASE_SECRET_NAME=$(printf '%s' "$ENV_JSON" | jq -er '.DATABASE_URL_SECRET_NAME')
DATABASE_URL_V2_ENABLED=$(printf '%s' "$ENV_JSON" | jq -er '.DATABASE_URL_V2_ENABLED')
if [[ "$DATABASE_SECRET_NAME" != "database-url" && "$DATABASE_SECRET_NAME" != "database-url-v2" ]]; then
	echo "Invalid DATABASE_URL secret selector."
	exit 1
fi
if [[ "$DATABASE_URL_V2_ENABLED" != "true" && "$DATABASE_URL_V2_ENABLED" != "false" ]]; then
	echo "Invalid DATABASE_URL v2 enablement flag."
	exit 1
fi
if [[ "$DATABASE_SECRET_NAME" == "database-url-v2" && "$DATABASE_URL_V2_ENABLED" != "true" ]]; then
	echo "DATABASE_URL v2 cannot be selected while disabled."
	exit 1
fi

# A deploy may adopt an already-staged selection, but must never become the
# mechanism that moves all workloads at once.  The guard is value-free and
# requires all five live references to match the source-controlled selector.
if ! jq -n \
	--arg resource_group "$RESOURCE_GROUP" \
	--arg selected_secret "$DATABASE_SECRET_NAME" \
	--arg subscription_id "${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID is required}" \
	--arg v2_enabled "$DATABASE_URL_V2_ENABLED" \
	'{resource_group: $resource_group, selected_secret: $selected_secret, subscription_id: $subscription_id, v2_enabled: $v2_enabled}' |
	python "$SCRIPT_DIR/database_url_live_guard.py"; then
	echo "Live DATABASE_URL state does not match the checked-in selector."
	exit 1
fi

# Verify only secret names and references: the Recall signing credential must
# exist exactly once on the API and nowhere on workers, beat, Flower or the
# migration job. The guard never requests or prints secret values.
if ! RECALL_WEBHOOK_SECRET_GUARD=$(printf '%s' "$ENV_JSON" |
	jq -er '.RECALL_WEBHOOK_SECRET | select(type == "string")'); then
	echo "Production Recall webhook signing credential is invalid."
	exit 1
fi
if ! jq -n \
	--arg resource_group "$RESOURCE_GROUP" \
	--arg subscription_id "${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID is required}" \
	'{resource_group: $resource_group, subscription_id: $subscription_id}' |
	RECALL_WEBHOOK_SECRET="$RECALL_WEBHOOK_SECRET_GUARD" \
	python "$SCRIPT_DIR/api_only_secret_live_guard.py"; then
	unset RECALL_WEBHOOK_SECRET_GUARD
	echo "Live API-only secret scope is not ready for application deployment."
	exit 1
fi
unset RECALL_WEBHOOK_SECRET_GUARD

# Must match variables.tf python_secret_vars. API-only secrets are provisioned
# by OpenTofu and must never be copied into the migration job.
COMMON_SECRET_VARS=(
	REDIS_URL SESSION_SECRET_KEY ENCRYPTION_KEY
	CLERK_PRIVATE_KEY
	SENTRY_DSN ASSEMBLY_API_KEY SMTP_URL
	AZURE_OPENAI_API_KEY
	AZURE_UPLOADS_ACCOUNT_NAME AZURE_UPLOADS_ACCOUNT_KEY AZURE_UPLOADS_ACCOUNT_URL
	AZURE_DEID_TENANT_ID AZURE_DEID_CLIENT_ID AZURE_DEID_CLIENT_SECRET AZURE_DEID_ENDPOINT
	RECALL_API_KEY
	ZOOM_CLIENT_ID ZOOM_CLIENT_SECRET ZOOM_SECRET_TOKEN
)

# The production migration job predates Terraform ownership. Keep its legacy
# public configuration allowlist explicit while rejecting API-only, billing,
# authority, and any other unexpected capabilities before Alembic can run.
MIGRATION_PUBLIC_VARS=(
	LOG_LEVEL EMAIL_FROM_ADDRESS PORT ALLOWED_HOST_LIST INTAKE_CHAT_DOMAIN
	AZURE_OPENAI_ENDPOINT
	AZURE_UPLOADS_CONTAINER_NAME AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME
	ZOOM_REDIRECT_URI ZOOM_SUCCESS_REDIRECT
)

verify_migration_job_template() {
	# Query only the fields needed for the contract; never request env values.
	local migration_template secret_env_names public_env_names
	if ! migration_template=$(az containerapp job show \
		-n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
		--query 'properties.template.containers[].{name:name,image:image,command:command,args:args,env:env[].{name:name,secretRef:secretRef}}' \
		-o json); then
		echo "Failed to inspect the migration job execution template."
		return 1
	fi
	secret_env_names=$(IFS=,; echo "DATABASE_URL,${COMMON_SECRET_VARS[*]}")
	public_env_names=$(IFS=,; echo "${MIGRATION_PUBLIC_VARS[*]}")
	if ! printf '%s' "$migration_template" |
		python "$SCRIPT_DIR/../scripts/migration_job_template_guard.py" \
			--expected-name "$MIGRATION_JOB" \
			--expected-image "$PRODUCTION_TAG" \
			--database-secret-name "$DATABASE_SECRET_NAME" \
			--secret-env-names "$secret_env_names" \
			--public-env-names "$public_env_names"; then
		echo "Migration job template is not approved for schema mutation."
		return 1
	fi
}

wait_for_migration_job_execution() {
	local execution_name=$1
	local phase=$2
	if [[ -z "$execution_name" ]]; then
		echo "$phase failed to start."
		return 1
	fi

	echo "$phase execution started: $execution_name"
	local attempts=0
	local max_attempts=60 # 5 minutes

	while [[ $attempts -lt $max_attempts ]]; do
		local exec_status
		exec_status=$(az containerapp job execution show \
			-n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
			--job-execution-name "$execution_name" \
			--query "properties.status" -o tsv 2>/dev/null)

		case "$exec_status" in
		Succeeded)
			echo "$phase completed successfully."
			return 0 ;;
		Failed|Stopped)
			echo "$phase failed with status: $exec_status"
			return 1 ;;
		*)
			echo "  $phase status=$exec_status (attempt $((attempts + 1))/$max_attempts)"
			sleep 5
			((attempts++)) ;;
		esac
	done

	echo "$phase timed out after $((max_attempts * 5)) seconds."
	return 1
}

run_migrations() {
	echo "Running database migrations with image $PRODUCTION_TAG..."

	local secrets_args=()

	for var in "${COMMON_SECRET_VARS[@]}"; do
		local value
		value=$(echo "$ENV_JSON" | jq -r ".\"$var\" // empty")
		[[ -z "$value" ]] && continue
		local secret_name
		secret_name=$(echo "$var" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
		secrets_args+=("${secret_name}=${value}")
	done

	# ACR admin creds so the job can pull the image.
	# These come from the workflow env (the SP running this script does not have
	# read access to the ACR resource, so az acr show/credential show would fail).
	local acr_server="${CONTAINER_REGISTRY_HOST:?CONTAINER_REGISTRY_HOST env is required}"
	local acr_user="${CONTAINER_REGISTRY_USERNAME:?CONTAINER_REGISTRY_USERNAME env is required}"
	local acr_pw="${CONTAINER_REGISTRY_PASSWORD:?CONTAINER_REGISTRY_PASSWORD env is required}"

	if ! az containerapp job show -n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" &>/dev/null; then
		echo "Migration job is missing; provision and review it before deploying."
		return 1
	fi
	local migration_container_name
	if ! migration_container_name=$(az containerapp job show \
		-n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
		--query "properties.template.containers[].name" -o tsv); then
		echo "Failed to inspect the migration job container template."
		return 1
	fi
	if [[ "$migration_container_name" != "$MIGRATION_JOB" ]]; then
		echo "Migration job must contain exactly one source-owned container."
		return 1
	fi

	echo "Updating existing migration job..."
	if [[ ${#secrets_args[@]} -gt 0 ]]; then
		if ! az containerapp job secret set -n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
			--secrets "${secrets_args[@]}"; then
			echo "Failed to configure migration job secrets."
			return 1
		fi
	fi
	if ! az containerapp job registry set -n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
		--server "$acr_server" --username "$acr_user" --password "$acr_pw"; then
		echo "Failed to configure migration job registry."
		return 1
	fi
	if ! az containerapp job update -n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
		--image "$PRODUCTION_TAG"; then
		echo "Failed to configure migration job image."
		return 1
	fi

	# The versioned database URL never enters an Azure CLI argument.  A strict
	# JSON subset is passed on stdin to the ARM reconciler.  The known-good
	# database-url value is not read from ENV_JSON or replaced; the reconciler
	# requires that rollback secret to remain present.
	if ! printf '%s' "$ENV_JSON" |
		jq -c '
			if .DATABASE_URL_V2_ENABLED == "true"
			 then {"database-url-v2": .DATABASE_URL_V2}
			 else {}
			 end
		' |
		python "$SCRIPT_DIR/database_url_cutover.py" \
			--subscription-id "$AZURE_SUBSCRIPTION_ID" \
			--resource-group "$RESOURCE_GROUP" \
			--confirm-subscription "$AZURE_SUBSCRIPTION_ID" \
			--confirm-resource-group "$RESOURCE_GROUP" \
			--selected-secret-name "$DATABASE_SECRET_NAME" \
			--reconcile-migration-secrets-stdin; then
		echo "Failed to reconcile migration database secrets."
		return 1
	fi

	# A plain job start executes this persistent template. Require the exact
	# reviewed contract before the read-only preflight and again immediately
	# before schema mutation so an intervening out-of-band edit fails closed.
	verify_migration_job_template || return 1

	# Override only this execution. Azure replaces the container definition when
	# an execution override is supplied, so explicitly restore the selected
	# DATABASE_URL secret reference. The reviewed job template retains its
	# Alembic arguments, but no schema mutation may start until the aggregate-only
	# duplicate check succeeds against the exact image and database secret.
	local preflight_execution
	if ! preflight_execution=$(az containerapp job start \
		-n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
		--container-name "$migration_container_name" \
		--image "$PRODUCTION_TAG" \
		--command /opt/venv/bin/python \
		--args /app/scripts/billing_migration_preflight.py \
		--env-vars "DATABASE_URL=secretref:$DATABASE_SECRET_NAME" \
		--query "name" -o tsv); then
		echo "Billing migration preflight failed to start."
		return 1
	fi
	if ! wait_for_migration_job_execution \
		"$preflight_execution" "Billing migration preflight"; then
		echo "Billing migration preflight blocked schema migration."
		return 1
	fi
	verify_migration_job_template || return 1

	local execution_name
	if ! execution_name=$(az containerapp job start \
		-n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
		--query "name" -o tsv); then
		echo "Failed to start migration job."
		return 1
	fi
	wait_for_migration_job_execution "$execution_name" "Migrations"
}

initiate_deployment() {
	local container_name=$1
	local git_sha=$2
	local image="${PRODUCTION_IMAGE}:${git_sha}"

	# All declared production workloads are required. In particular, shipping
	# the inbox without the scheduler would remove crash recovery.
	if ! az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" &>/dev/null; then
		echo "Required container $container_name does not exist; apply infrastructure first."
		return 1
	fi

	echo "Initiating deployment for $container_name with image $image..."
	az containerapp update -n "$container_name" -g "$RESOURCE_GROUP" -i "$image" --revision-suffix "$REVISION_STRING"

	if [[ $? -ne 0 ]]; then
		echo "Deployment initiation failed for $container_name."
		return 1
	fi

	echo "Deployment initiated for $container_name."
	return 0
}

# Function to check deployment status
check_deployment() {
	local container_name=$1
	local git_sha=$2
	local expected_image="${PRODUCTION_IMAGE}:${git_sha}"
	local expected_revision_name="${container_name}--${REVISION_STRING}"

	if ! az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" &>/dev/null; then
		echo "Required container $container_name disappeared during deployment."
		return 1
	fi

	# Initialize backoff variables
	local attempts=0
	local max_attempts=60 # 5 minutes / 5 seconds = 60 attempts

	echo "Checking deployment status for $container_name..."

	while [[ $attempts -lt $max_attempts ]]; do
		# Always inspect the latest revision specifically, not `revision list[0]`.
		# `revision list` returns active revisions in unspecified order; when a
		# previous deploy left a failed-but-still-active revision behind, [0]
		# can resolve to the OLD revision and the script never sees that the
		# revision we just pushed is failing — burning the entire retry budget.
		# `latestRevisionName` is the most recently created revision, which is
		# the one our `az containerapp update -i` just produced.
		local latest_rev_name revision_json current_image health_state running_state provisioning_state
		latest_rev_name=$(az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" --query "properties.latestRevisionName" -o tsv)

		if [[ -z "$latest_rev_name" ]]; then
			echo "  $container_name: could not read latestRevisionName (attempt $((attempts + 1))/$max_attempts)"
			sleep 5
			((attempts++))
			continue
		fi
		if [[ "$latest_rev_name" != "$expected_revision_name" ]]; then
			echo "  $container_name: latest revision is not this deployment attempt yet (attempt $((attempts + 1))/$max_attempts)"
			sleep 5
			((attempts++))
			continue
		fi

		revision_json=$(az containerapp revision show -n "$container_name" -g "$RESOURCE_GROUP" \
			--revision "$latest_rev_name" \
			--query "{image:properties.template.containers[0].image,health:properties.healthState,running:properties.runningState,provisioning:properties.provisioningState}" -o json 2>/dev/null)

		current_image=$(echo "$revision_json" | jq -r '.image // empty')
		health_state=$(echo "$revision_json" | jq -r '.health // empty')
		running_state=$(echo "$revision_json" | jq -r '.running // empty')
		provisioning_state=$(echo "$revision_json" | jq -r '.provisioning // empty')

		# Image mismatch means Azure hasn't yet picked up our update — keep polling.
		if [[ "$current_image" != "$expected_image" ]]; then
			echo "  $container_name: latest revision still $current_image, waiting for $expected_image (attempt $((attempts + 1))/$max_attempts)"
			sleep 5
			((attempts++))
			continue
		fi

		# Fail fast: once Azure has marked the revision as Failed, no amount of
		# polling will revive it. Surface the error now instead of burning the
		# full 5-minute retry budget.
		if [[ "$provisioning_state" == "Failed" || "$running_state" == "Failed" ]]; then
			echo "$container_name: revision $latest_rev_name failed (provisioning=$provisioning_state, running=$running_state, health=$health_state)"
			return 1
		fi

		# Healthy is the actual signal we care about — it means probes pass.
		# runningState varies by container (Running for jmon/app, RunningAtMaxScale
		# for worker/scheduler), so we don't pin a specific value here.
		if [[ "$health_state" == "Healthy" && "$provisioning_state" == "Provisioned" ]]; then
			echo "$container_name deployment is successful."
			return 0
		fi

		echo "  $container_name: provisioning=$provisioning_state running=$running_state health=$health_state (attempt $((attempts + 1))/$max_attempts)"

		sleep 5
		((attempts++))
	done

	echo "Deployment check failed for $container_name after $max_attempts attempts."
	return 1
}

wait_for_incompatible_worker_replicas_to_drain() {
	local container_name=$1
	local git_sha=$2
	local expected_image="${PRODUCTION_IMAGE}:${git_sha}"
	local attempts=0
	local max_attempts=60 # 5 minutes
	local active_revisions_mode

	if ! active_revisions_mode=$(az containerapp show \
		-n "$container_name" -g "$RESOURCE_GROUP" \
		--query "properties.configuration.activeRevisionsMode" -o tsv); then
		echo "Failed to read worker revision mode while proving drain completion."
		return 1
	fi
	if [[ "$active_revisions_mode" != "Single" ]]; then
		echo "Worker drain proof requires Single active revision mode; found '$active_revisions_mode'."
		return 1
	fi

	echo "Waiting for worker replicas on older images to drain..."

	while [[ $attempts -lt $max_attempts ]]; do
		local revisions_json expected_revision_count incompatible_revisions remaining_replicas
		if ! revisions_json=$(az containerapp revision list \
			-n "$container_name" -g "$RESOURCE_GROUP" \
			--query "[].{name:name,image:properties.template.containers[0].image}" \
			-o json); then
			echo "Failed to list worker revisions while proving drain completion."
			return 1
		fi
		if ! expected_revision_count=$(printf '%s' "$revisions_json" | jq -r \
			--arg expected_image "$expected_image" \
			'if type != "array" then error("revision list was not an array") else [.[] | select(.image == $expected_image)] | length end'); then
			echo "Failed to prove the expected worker revision is present."
			return 1
		fi
		if [[ ! "$expected_revision_count" =~ ^[0-9]+$ || "$expected_revision_count" -eq 0 ]]; then
			echo "Expected worker image $expected_image is absent from the revision list; refusing to deploy the API."
			return 1
		fi
		if ! incompatible_revisions=$(printf '%s' "$revisions_json" | jq -r \
			--arg expected_image "$expected_image" \
			'if type != "array" then error("revision list was not an array") else .[] | select(.image != $expected_image) | .name end'); then
			echo "Failed to identify incompatible worker revisions."
			return 1
		fi

		remaining_replicas=0
		for revision_name in $incompatible_revisions; do
			local replica_count
			if ! replica_count=$(az containerapp replica list \
				-n "$container_name" -g "$RESOURCE_GROUP" \
				--revision "$revision_name" --query "length(@)" -o tsv); then
				echo "Failed to inspect replicas for worker revision $revision_name."
				return 1
			fi
			if [[ ! "$replica_count" =~ ^[0-9]+$ ]]; then
				echo "Invalid replica count for worker revision $revision_name."
				return 1
			fi
			remaining_replicas=$((remaining_replicas + replica_count))
		done

		if [[ "$remaining_replicas" -eq 0 ]]; then
			echo "All worker replicas on older images have drained."
			return 0
		fi

		echo "  $remaining_replicas incompatible worker replica(s) remain (attempt $((attempts + 1))/$max_attempts)"
		sleep 5
		((attempts++))
	done

	echo "Worker drain proof failed after $((max_attempts * 5)) seconds. The API was not deployed."
	return 1
}

# Run migrations before deploying new containers
run_migrations
if [[ $? -ne 0 ]]; then
	echo "Migration failed. Aborting deployment."
	exit 1
fi

# Deploy and verify consumers/recovery before exposing the new API revision.
# This preserves rolling compatibility: migrations land first, then workers
# and the scheduler can consume both old and new work before the API produces
# anything new.  Job Monitor is observational and updates last.
for container in $WORKER_SCHEDULER_CONTAINERS; do
	initiate_deployment "$container" "$GIT_SHA"
	if [[ $? -ne 0 ]]; then
		echo "Deployment initiation failed. Exiting."
		exit 1
	fi
	check_deployment "$container" "$GIT_SHA"
	if [[ $? -ne 0 ]]; then
		echo "Deployment failed for $container. Exiting."
		exit 1
	fi
	if [[ "$container" == "$WORKER_CONTAINER" ]]; then
		wait_for_incompatible_worker_replicas_to_drain "$container" "$GIT_SHA" || exit 1
	fi
done

initiate_deployment "$API_CONTAINER" "$GIT_SHA" || exit 1
check_deployment "$API_CONTAINER" "$GIT_SHA" || exit 1

initiate_deployment "$JMON_CONTAINER" "$GIT_SHA" || exit 1
check_deployment "$JMON_CONTAINER" "$GIT_SHA" || exit 1

echo "All deployments are successful for Git SHA $GIT_SHA."
exit 0
