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

# TODO(mbianco) these should really be pulled from the ENV or somewhere more stable
# Define variables
RESOURCE_GROUP="cosmaneura-prod"
MIGRATION_JOB="cosmaneura-prod-migrate"
CONTAINERS="cosmaneura-prod-app cosmaneura-prod-worker cosmaneura-prod-scheduler cosmaneura-prod-app-jmon"
PRODUCTION_IMAGE="cosmaneuraprodregistry.azurecr.io/cosmaneura/cosmaneura"

# Check for Git SHA input and validate it exists in repo
if [[ -z "$GIT_SHA" ]]; then
	echo "Error: Please provide a Git SHA as an argument."
	exit 1
fi

if ! git rev-parse --quiet --verify "$GIT_SHA^{commit}" >/dev/null; then
	echo "Error: $GIT_SHA is not a valid commit in this repository."
	exit 1
fi

echo "Azure Acccount Information:"
az account show

echo "All Available Containers:"
az containerapp list --query "[].name"

# Convert potential short SHA to full SHA
GIT_SHA=$(git rev-parse "$GIT_SHA")
PRODUCTION_TAG="$PRODUCTION_IMAGE:$GIT_SHA"

if ! docker manifest inspect "$PRODUCTION_TAG" >/dev/null 2>&1; then
	echo "Error: Image $PRODUCTION_TAG does not exist"
	exit 1
fi

# Function to generate a revision string for the Git SHA
gen_string_for_sha() {
	local sha="$1"
	local sys_date count short_sha

	sys_date=$(date +%Y-%m-%d)
	count=$(git rev-list --count "$sha")
	short_sha=$(echo "$sha" | cut -c1-7)

	echo "${sys_date}-v${count}-${short_sha}"
}

# Generate the revision string
# Note that if you attempt to deploy the same image twice, the revision string will be the same
# and probably cause deployment errors, so you'll need to manually adjust this.
#
# Also, long application names can limit your revision string:
# 'A revision name must consist of lower case alphanumeric characters or '-', start with an alphabetic character, and end with an alphanumeric character. The length of the revision name must not be more than a combined 54 characters with the container app name.'
REVISION_STRING=$(gen_string_for_sha "$GIT_SHA")

echo "Revision string: $REVISION_STRING"

# Read all production secrets from 1Password (same mechanism as tofu uses).
# Must stay in sync with variables.tf python_secret_vars / python_public_vars.
ENV_JSON=$(cd "$SCRIPT_DIR/.." && just direnv_export .env.production.backend)

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
if ! jq -n \
	--arg resource_group "$RESOURCE_GROUP" \
	--arg subscription_id "${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID is required}" \
	'{resource_group: $resource_group, subscription_id: $subscription_id}' |
	python "$SCRIPT_DIR/api_only_secret_live_guard.py"; then
	echo "Live API-only secret scope is not ready for application deployment."
	exit 1
fi

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

	local execution_name
	execution_name=$(az containerapp job start \
		-n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
		--query "name" -o tsv)

	if [[ -z "$execution_name" ]]; then
		echo "Failed to start migration job."
		return 1
	fi

	echo "Migration job execution started: $execution_name"

	local attempts=0
	local max_attempts=60  # 5 minutes

	while [[ $attempts -lt $max_attempts ]]; do
		local exec_status
		exec_status=$(az containerapp job execution show \
			-n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
			--job-execution-name "$execution_name" \
			--query "properties.status" -o tsv 2>/dev/null)

		case "$exec_status" in
			Succeeded)
				echo "Migrations completed successfully."
				return 0 ;;
			Failed|Stopped)
				echo "Migration job failed with status: $exec_status"
				return 1 ;;
			*)
				echo "  migration status=$exec_status (attempt $((attempts + 1))/$max_attempts)"
				sleep 5
				((attempts++)) ;;
		esac
	done

	echo "Migration job timed out after $((max_attempts * 5)) seconds."
	return 1
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

# Run migrations before deploying new containers
run_migrations
if [[ $? -ne 0 ]]; then
	echo "Migration failed. Aborting deployment."
	exit 1
fi

# Deploy all containers first
for container in $CONTAINERS; do
	initiate_deployment "$container" "$GIT_SHA"
	if [[ $? -ne 0 ]]; then
		echo "Deployment initiation failed. Exiting."
		exit 1
	fi
done

# Check deployment status for all containers
for container in $CONTAINERS; do
	check_deployment "$container" "$GIT_SHA"
	if [[ $? -ne 0 ]]; then
		echo "Deployment failed for $container. Exiting."
		exit 1
	fi
done

echo "All deployments are successful for Git SHA $GIT_SHA."
exit 0
