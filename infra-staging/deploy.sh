#!/usr/bin/env bash

# Description: this insane mess deploys already-built containers to staging
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
RESOURCE_GROUP="cosmaneura-staging"
ENVIRONMENT_NAME="cosmaneura-staging-app-environment"
MIGRATION_JOB="cosmaneura-staging-migrate"
BACKEND_CONTAINERS="cosmaneura-staging-worker cosmaneura-staging-scheduler cosmaneura-staging-app-jmon"
API_CONTAINER="cosmaneura-staging-app"
CONTAINERS="$BACKEND_CONTAINERS $API_CONTAINER"
SCHEDULER_CONTAINER="cosmaneura-staging-scheduler"
GRANT_SECRET_ENV_NAME="SOVEREIGN_RECORD_GRANT_SECRET"
GRANT_SECRET_RESOURCE_NAME="sovereign-record-grant-secret"
RECALL_SECRET_ENV_NAME="RECALL_WEBHOOK_SECRET"
RECALL_SECRET_RESOURCE_NAME="recall-webhook-secret"
STAGING_IMAGE="cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura"

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
STAGING_TAG="$STAGING_IMAGE:$GIT_SHA"

if ! docker manifest inspect "$STAGING_TAG" >/dev/null 2>&1; then
	echo "Error: Image $STAGING_TAG does not exist"
	exit 1
fi

ACR_NAME=$(echo "$STAGING_IMAGE" | cut -d'.' -f1)
ACR_USERNAME=$(az acr credential show -n "$ACR_NAME" --query "username" -o tsv)
ACR_PASSWORD=$(az acr credential show -n "$ACR_NAME" --query "passwords[0].value" -o tsv)
ACR_SERVER=$(echo "$STAGING_IMAGE" | cut -d'/' -f1)

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

# Read all staging secrets from 1Password once (same mechanism as tofu uses for production).
# Must stay in sync with variables.tf python_secret_vars / python_public_vars.
if ! ENV_JSON=$(cd "$SCRIPT_DIR/.." && just direnv_export .env.staging.backend); then
	echo "Failed to load staging deployment configuration."
	exit 1
fi

ENABLE_SOVEREIGN_RECORD_GRANTS=$(
	echo "$ENV_JSON" | jq -r '.ENABLE_SOVEREIGN_RECORD_GRANTS // "false"'
)
if [[ "$ENABLE_SOVEREIGN_RECORD_GRANTS" != "true" && \
	"$ENABLE_SOVEREIGN_RECORD_GRANTS" != "false" ]]; then
	echo "ENABLE_SOVEREIGN_RECORD_GRANTS must be true or false."
	exit 1
fi
if [[ "$ENABLE_SOVEREIGN_RECORD_GRANTS" == "true" && \
	-z "$(echo "$ENV_JSON" | jq -r '.SOVEREIGN_RECORD_GRANT_SECRET // empty')" ]]; then
	echo "Staging record grants were enabled without a configured key."
	exit 1
fi

if ! echo "$ENV_JSON" |
	jq -e '.RECALL_WEBHOOK_SECRET | type == "string" and length > 0' >/dev/null; then
	echo "Staging Recall webhook signing secret is not configured."
	exit 1
fi

DURABLE_WEBHOOK_INBOX_ENABLED=$(
	echo "$ENV_JSON" | jq -r '.DURABLE_WEBHOOK_INBOX_ENABLED // "false"'
)
if [[ "$DURABLE_WEBHOOK_INBOX_ENABLED" != "true" && \
	"$DURABLE_WEBHOOK_INBOX_ENABLED" != "false" ]]; then
	echo "DURABLE_WEBHOOK_INBOX_ENABLED must be true or false."
	exit 1
fi

SCHEDULER_COMMAND=$(yq -r '.scheduler // ""' "$SCRIPT_DIR/../Procfile")
if [[ -z "$SCHEDULER_COMMAND" ]]; then
	echo "Procfile scheduler command is missing."
	exit 1
fi

# Recovery is a deployment prerequisite, not a best-effort extra. Fail before
# migrations or any Container App mutation when a required workload is absent.
for container in $CONTAINERS; do
	if ! az containerapp show -n "$container" -g "$RESOURCE_GROUP" &>/dev/null; then
		echo "Required staging container $container is missing; apply infrastructure first."
		exit 1
	fi
done

# Must match variables.tf python_secret_vars
COMMON_SECRET_VARS=(
	DATABASE_URL REDIS_URL SESSION_SECRET_KEY ENCRYPTION_KEY
	CLERK_PRIVATE_KEY
	SENTRY_DSN ASSEMBLY_API_KEY SMTP_URL
	AZURE_OPENAI_API_KEY
	AZURE_UPLOADS_ACCOUNT_NAME AZURE_UPLOADS_ACCOUNT_KEY AZURE_UPLOADS_ACCOUNT_URL
	AZURE_DEID_TENANT_ID AZURE_DEID_CLIENT_ID AZURE_DEID_CLIENT_SECRET AZURE_DEID_ENDPOINT
	RECALL_API_KEY
	ZOOM_CLIENT_ID ZOOM_CLIENT_SECRET ZOOM_SECRET_TOKEN
)

# Capability-bearing secrets that only the HTTP API may receive. Never append
# these to the migration, worker, scheduler, or job-monitor environments.
API_ONLY_SECRET_VARS=(
	RECALL_WEBHOOK_SECRET
)

# Must match variables.tf python_public_vars
PUBLIC_VARS=(
	PYTHON_ENV
	LOG_LEVEL EMAIL_FROM_ADDRESS PORT ALLOWED_HOST_LIST INTAKE_CHAT_DOMAIN
	AZURE_OPENAI_ENDPOINT
	AZURE_UPLOADS_CONTAINER_NAME AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME
	ZOOM_REDIRECT_URI ZOOM_SUCCESS_REDIRECT
	RECALL_API_BASE
	COOKIE_DOMAIN
	SCRIBE_HANDOFF_ENABLED ENTRA_TENANT_ID SCRIBE_API_AUDIENCE SCRIBE_CALLER_CLIENT_ID
	SCRIBE_RECONCILER_CALLER_CLIENT_ID
	DURABLE_WEBHOOK_INBOX_ENABLED
)

run_migrations() {
	echo "Running database migrations with image $STAGING_TAG..."

	# Build secrets and env var args from the same source as the app containers.
	local secrets_args=()
	local env_args=()

	for var in "${COMMON_SECRET_VARS[@]}"; do
		local value
		value=$(echo "$ENV_JSON" | jq -r ".\"$var\" // empty")
		[[ -z "$value" ]] && continue
		local secret_name
		secret_name=$(echo "$var" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
		secrets_args+=("${secret_name}=${value}")
		env_args+=("${var}=secretref:${secret_name}")
	done

	for var in "${PUBLIC_VARS[@]}"; do
		local value
		value=$(echo "$ENV_JSON" | jq -r ".\"$var\" // empty")
		[[ -n "$value" ]] && env_args+=("${var}=${value}")
	done

	if az containerapp job show -n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" &>/dev/null; then
		echo "Deleting existing migration job to recreate with fresh config..."
		az containerapp job delete -n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" --yes
	fi

	echo "Creating migration job..."
	az containerapp job create \
		--name "$MIGRATION_JOB" \
		--resource-group "$RESOURCE_GROUP" \
		--environment "$ENVIRONMENT_NAME" \
		--trigger-type Manual \
		--replica-timeout 300 \
		--replica-retry-limit 0 \
		--image "$STAGING_TAG" \
		--args "/opt/venv/bin/alembic upgrade head" \
		--cpu 0.5 --memory 1Gi \
		--secrets "${secrets_args[@]}" \
		--env-vars "${env_args[@]}" \
		--registry-server "$ACR_SERVER" \
		--registry-username "$ACR_USERNAME" \
		--registry-password "$ACR_PASSWORD"

	if [[ $? -ne 0 ]]; then
		echo "Failed to configure migration job."
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

record_grants_enabled_for() {
	local container_name=$1
	[[ "$container_name" == "$API_CONTAINER" && \
		"$ENABLE_SOVEREIGN_RECORD_GRANTS" == "true" ]]
}

initiate_deployment() {
	local container_name=$1
	local git_sha=$2
	local image="${STAGING_IMAGE}:${git_sha}"

	# Build secrets and env var args — only include vars that have a value in 1Password.
	# We skip missing vars so we never reference a secretref that doesn't exist.
	local secrets_args=()
	local env_args=()
	local container_secret_vars=("${COMMON_SECRET_VARS[@]}")
	if [[ "$container_name" == "$API_CONTAINER" ]]; then
		container_secret_vars+=("${API_ONLY_SECRET_VARS[@]}")
	fi
	if record_grants_enabled_for "$container_name"; then
		container_secret_vars+=(SOVEREIGN_RECORD_GRANT_SECRET)
	fi

	for var in "${container_secret_vars[@]}"; do
		local value
		value=$(echo "$ENV_JSON" | jq -r ".\"$var\" // empty")
		[[ -z "$value" ]] && continue
		local secret_name
		secret_name=$(echo "$var" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
		secrets_args+=("${secret_name}=${value}")
		env_args+=("${var}=secretref:${secret_name}")
	done

	for var in "${PUBLIC_VARS[@]}"; do
		local value
		value=$(echo "$ENV_JSON" | jq -r ".\"$var\" // empty")
		[[ -n "$value" ]] && env_args+=("${var}=${value}")
	done

	echo "Initiating deployment for $container_name with image $image..."

	# Secrets must be set separately; az containerapp update does not support --secrets.
	if [[ ${#secrets_args[@]} -gt 0 ]]; then
		if ! az containerapp secret set -n "$container_name" -g "$RESOURCE_GROUP" \
			--secrets "${secrets_args[@]}"; then
			echo "Failed to configure secrets for $container_name."
			return 1
		fi
	fi

	# Single update call: image + env vars + revision suffix. Reconcile the
	# capability out of every workload except an explicitly enabled API.
	local update_args=(
		-i "$image"
		--revision-suffix "$REVISION_STRING"
		--set-env-vars "${env_args[@]}"
	)
	local remove_env_vars=()
	if ! record_grants_enabled_for "$container_name"; then
		remove_env_vars+=("$GRANT_SECRET_ENV_NAME")
	fi
	if [[ "$container_name" != "$API_CONTAINER" ]]; then
		remove_env_vars+=("$RECALL_SECRET_ENV_NAME")
	fi
	if [[ ${#remove_env_vars[@]} -gt 0 ]]; then
		update_args+=(--remove-env-vars "${remove_env_vars[@]}")
	fi
	if [[ "$container_name" == "$SCHEDULER_CONTAINER" ]]; then
		update_args+=(--args "$SCHEDULER_COMMAND")
	fi
	az containerapp update -n "$container_name" -g "$RESOURCE_GROUP" \
		"${update_args[@]}"

	if [[ $? -ne 0 ]]; then
		echo "Deployment initiation failed for $container_name."
		return 1
	fi

	echo "Deployment initiated for $container_name."
	return 0
}

remove_disabled_record_grant_secret() {
	local container_name=$1
	if record_grants_enabled_for "$container_name"; then
		return 0
	fi

	local secret_count
	secret_count=$(az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" \
		--query "properties.configuration.secrets[?name=='$GRANT_SECRET_RESOURCE_NAME'] | length(@)" \
		-o tsv) || return 1
	if [[ "$secret_count" != "0" ]]; then
		az containerapp secret remove -n "$container_name" -g "$RESOURCE_GROUP" \
			--secret-names "$GRANT_SECRET_RESOURCE_NAME"
	fi
}

remove_disallowed_recall_secret() {
	local container_name=$1
	if [[ "$container_name" == "$API_CONTAINER" ]]; then
		return 0
	fi
	local secret_count
	secret_count=$(az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" \
		--query "properties.configuration.secrets[?name=='$RECALL_SECRET_RESOURCE_NAME'] | length(@)" \
		-o tsv) || return 1
	if [[ "$secret_count" != "0" ]]; then
		az containerapp secret remove -n "$container_name" -g "$RESOURCE_GROUP" \
			--secret-names "$RECALL_SECRET_RESOURCE_NAME"
	fi
}

# Function to check deployment status
check_deployment() {
	local container_name=$1
	local git_sha=$2
	local expected_image="${STAGING_IMAGE}:${git_sha}"

	# Initialize backoff variables
	local attempts=0
	local max_attempts=60 # 5 minutes / 5 seconds = 60 attempts

	echo "Checking deployment status for $container_name..."

	while [[ $attempts -lt $max_attempts ]]; do
		# Check provisioning state
		provisioning_state=$(az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" --query "properties.provisioningState" -o tsv)
		current_image=$(az containerapp revision list -n "$container_name" -g "$RESOURCE_GROUP" --query "[0].properties.template.containers[0].image" -o tsv)
		running_state=$(az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" --query "properties.runningStatus" -o tsv)

		# should never be empty, error if so
		if [[ -z "$provisioning_state" ]] || [[ -z "$current_image" ]] || [[ -z "$running_state" ]]; then
			echo "Error: Failed to get complete status information for $container_name"
			echo "Provisioning state: $provisioning_state"
			echo "Current image: $current_image"
			echo "Running state: $running_state"
			return 1
		fi

		# Validate all conditions
		if [[ "$provisioning_state" == "Succeeded" && "$current_image" == "$expected_image" && ( "$running_state" == "Running" || "$running_state" == "RunningAtMaxScale" ) ]]; then
			if [[ "$container_name" == "$SCHEDULER_CONTAINER" ]]; then
				local live_scheduler_command
				live_scheduler_command=$(az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" \
					--query "properties.template.containers[0].args[0]" -o tsv)
				if [[ "$live_scheduler_command" != "$SCHEDULER_COMMAND" ]]; then
					echo "Scheduler command does not match the checked-in Procfile."
					return 1
				fi
			fi
			echo "$container_name deployment is successful."
			return 0
		fi

		echo "Retrying deployment status for $container_name in 5 seconds... (Attempt $((attempts + 1))/$max_attempts)"

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

# Deploy and verify recovery workloads before making the new API revision live.
for container in $BACKEND_CONTAINERS; do
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
done

initiate_deployment "$API_CONTAINER" "$GIT_SHA" || exit 1
check_deployment "$API_CONTAINER" "$GIT_SHA" || exit 1

# A false gate is a real revocation, not merely a refusal to add the key on the
# next revision. Remove any stale app-level secret after the new revisions no
# longer reference it.
for container in $CONTAINERS; do
	remove_disabled_record_grant_secret "$container"
	if [[ $? -ne 0 ]]; then
		echo "Failed to reconcile record-grant scope for $container."
		exit 1
	fi
	remove_disallowed_recall_secret "$container"
	if [[ $? -ne 0 ]]; then
		echo "Failed to reconcile Recall signing-secret scope for $container."
		exit 1
	fi
done

echo "All deployments are successful for Git SHA $GIT_SHA."
exit 0
