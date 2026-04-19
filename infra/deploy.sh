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
ENVIRONMENT_NAME="cosmaneura-prod-app-environment"
MIGRATION_JOB="cosmaneura-prod-migrate"
CONTAINERS="cosmaneura-prod-app cosmaneura-prod-worker cosmaneura-prod-app-jmon"
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

# Must match variables.tf python_secret_vars
SECRET_VARS=(
	DATABASE_URL REDIS_URL SESSION_SECRET_KEY ENCRYPTION_KEY
	CLERK_PRIVATE_KEY SENTRY_DSN ASSEMBLY_API_KEY SMTP_URL
	AZURE_OPENAI_API_KEY
	AZURE_UPLOADS_ACCOUNT_NAME AZURE_UPLOADS_ACCOUNT_KEY AZURE_UPLOADS_ACCOUNT_URL
	AZURE_DEID_TENANT_ID AZURE_DEID_CLIENT_ID AZURE_DEID_CLIENT_SECRET AZURE_DEID_ENDPOINT
	RECALL_API_KEY
	ZOOM_CLIENT_ID ZOOM_CLIENT_SECRET ZOOM_SECRET_TOKEN
)

# Must match variables.tf python_public_vars
PUBLIC_VARS=(
	LOG_LEVEL EMAIL_FROM_ADDRESS PORT ALLOWED_HOST_LIST INTAKE_CHAT_DOMAIN
	AZURE_OPENAI_ENDPOINT
	AZURE_UPLOADS_CONTAINER_NAME AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME
	ZOOM_REDIRECT_URI ZOOM_SUCCESS_REDIRECT
)

run_migrations() {
	echo "Running database migrations with image $PRODUCTION_TAG..."

	local secrets_args=()
	local env_args=()

	for var in "${SECRET_VARS[@]}"; do
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

	# ACR admin creds so the job can pull the image.
	# These come from the workflow env (the SP running this script does not have
	# read access to the ACR resource, so az acr show/credential show would fail).
	local acr_server="${CONTAINER_REGISTRY_HOST:?CONTAINER_REGISTRY_HOST env is required}"
	local acr_user="${CONTAINER_REGISTRY_USERNAME:?CONTAINER_REGISTRY_USERNAME env is required}"
	local acr_pw="${CONTAINER_REGISTRY_PASSWORD:?CONTAINER_REGISTRY_PASSWORD env is required}"

	if az containerapp job show -n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" &>/dev/null; then
		echo "Updating existing migration job..."
		if [[ ${#secrets_args[@]} -gt 0 ]]; then
			az containerapp job secret set -n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
				--secrets "${secrets_args[@]}"
		fi
		az containerapp job registry set -n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
			--server "$acr_server" --username "$acr_user" --password "$acr_pw"
		az containerapp job update -n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
			--image "$PRODUCTION_TAG"
	else
		echo "Creating migration job for the first time..."
		az containerapp job create \
			--name "$MIGRATION_JOB" \
			--resource-group "$RESOURCE_GROUP" \
			--environment "$ENVIRONMENT_NAME" \
			--trigger-type Manual \
			--replica-timeout 300 \
			--replica-retry-limit 0 \
			--image "$PRODUCTION_TAG" \
			--command "uv" --args "run alembic upgrade head" \
			--cpu 0.5 --memory 1Gi \
			--registry-server "$acr_server" \
			--registry-username "$acr_user" \
			--registry-password "$acr_pw" \
			--secrets "${secrets_args[@]}" \
			--env-vars "${env_args[@]}"
	fi

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

initiate_deployment() {
	local container_name=$1
	local git_sha=$2
	local image="${PRODUCTION_IMAGE}:${git_sha}"

	echo "Initiating deployment for $container_name with image $image..."
	az containerapp update -n "$container_name" -g "$RESOURCE_GROUP" -i "$image" --revision-suffix "$REVISION_STRING"
	# --set-environment-variables "REVISION=$REVISION_STRING"

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
		if [[ "$provisioning_state" == "Succeeded" && "$current_image" == "$expected_image" && "$running_state" == "Running" ]]; then
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
