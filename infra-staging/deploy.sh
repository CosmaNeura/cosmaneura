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
source "$SCRIPT_DIR/../scripts/deploy_revision_suffix.sh"

# TODO(mbianco) these should really be pulled from the ENV or somewhere more stable
# Define variables
RESOURCE_GROUP="cosmaneura-staging"
ENVIRONMENT_NAME="cosmaneura-staging-app-environment"
MIGRATION_JOB="cosmaneura-staging-migrate"
BACKEND_CONTAINERS="cosmaneura-staging-worker cosmaneura-staging-scheduler"
API_CONTAINER="cosmaneura-staging-app"
CRITICAL_CONTAINERS="$BACKEND_CONTAINERS $API_CONTAINER"
WORKER_CONTAINER="cosmaneura-staging-worker"
CONTAINERS="$BACKEND_CONTAINERS $API_CONTAINER"
SCHEDULER_CONTAINER="cosmaneura-staging-scheduler"
GRANT_SECRET_ENV_NAME="SOVEREIGN_RECORD_GRANT_SECRET"
GRANT_SECRET_RESOURCE_NAME="sovereign-record-grant-secret"
RECALL_SECRET_ENV_NAME="RECALL_WEBHOOK_SECRET"
RECALL_SECRET_RESOURCE_NAME="recall-webhook-secret"
STAGING_IMAGE="cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura"
STAGING_WORKER_COMMAND=$(yq -r '.worker' "$SCRIPT_DIR/../Procfile")
SCHEDULER_COMMAND=$(yq -r '.scheduler // ""' "$SCRIPT_DIR/../Procfile")

# Preserve explicit deployment controls before direnv renders the secret
# bundle. `direnv export` emits a delta, so values inherited unchanged by the
# rendered environment may be absent from its JSON output.
ENABLE_PROVIDER_V7="${ENABLE_PROVIDER_V7:-false}"
ENABLE_SOVEREIGN_RECORD_GRANTS="${ENABLE_SOVEREIGN_RECORD_GRANTS:-false}"

if [[ "$ENABLE_PROVIDER_V7" != "true" && "$ENABLE_PROVIDER_V7" != "false" ]]; then
	echo "ENABLE_PROVIDER_V7 must be true or false."
	exit 1
fi

# Resolve protected configuration before invoking git, Docker, Azure, or any
# other provider-facing subprocess. Keep the resolved JSON in this shell only,
# then drop the ambient capabilities immediately. ENV_JSON is deliberately not
# exported, so child processes receive only values passed to them explicitly.
if ! ENV_JSON=$(cd "$SCRIPT_DIR/.." && just direnv_export .env.staging.backend); then
	echo "Failed to load staging deployment configuration."
	exit 1
fi
unset OP_SERVICE_ACCOUNT_TOKEN RECALL_WEBHOOK_SECRET

# Keep the provider-v7 runtime gates synchronized even when the inherited
# control was omitted from direnv's delta.
if ! ENV_JSON=$(echo "$ENV_JSON" | jq -c \
	--arg enabled "$ENABLE_PROVIDER_V7" \
	'. + {
		AUTHORITY_KERNEL_ENABLED: $enabled,
		AGENT_API_ENABLED: $enabled
	}'); then
	echo "Failed to reconcile provider V7 staging configuration."
	exit 1
fi

if [[ "$ENABLE_SOVEREIGN_RECORD_GRANTS" != "true" && \
	"$ENABLE_SOVEREIGN_RECORD_GRANTS" != "false" ]]; then
	echo "ENABLE_SOVEREIGN_RECORD_GRANTS must be true or false."
	exit 1
fi

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
STAGING_TAG="$STAGING_IMAGE:$GIT_SHA"

if ! REVISION_STRING=$(generate_deploy_revision_suffix "$GIT_SHA"); then
	exit 1
fi
if ! validate_deploy_revision_suffix "$REVISION_STRING" $CONTAINERS; then
	exit 1
fi

echo "Revision string: $REVISION_STRING"

echo "Azure Acccount Information:"
az account show

echo "All Available Containers:"
az containerapp list --query "[].name"

if ! docker manifest inspect "$STAGING_TAG" >/dev/null 2>&1; then
	echo "Error: Image $STAGING_TAG does not exist"
	exit 1
fi

ACR_NAME=$(echo "$STAGING_IMAGE" | cut -d'.' -f1)
ACR_USERNAME=$(az acr credential show -n "$ACR_NAME" --query "username" -o tsv)
ACR_PASSWORD=$(az acr credential show -n "$ACR_NAME" --query "passwords[0].value" -o tsv)
ACR_SERVER=$(echo "$STAGING_IMAGE" | cut -d'/' -f1)

if [[ "$ENABLE_SOVEREIGN_RECORD_GRANTS" == "true" && \
	-z "$(echo "$ENV_JSON" | jq -r '.SOVEREIGN_RECORD_GRANT_SECRET // empty')" ]]; then
	echo "Staging record grants were enabled without a configured key."
	exit 1
fi

STRIPE_INTEGRATION_ENABLED=$(echo "$ENV_JSON" | jq -r '.STRIPE_INTEGRATION_ENABLED // "false"')
STRIPE_BILLING_ENABLED=$(echo "$ENV_JSON" | jq -r '.STRIPE_BILLING_ENABLED // "false"')
for flag in "$STRIPE_INTEGRATION_ENABLED" "$STRIPE_BILLING_ENABLED"; do
	if [[ "$flag" != "true" && "$flag" != "false" ]]; then
		echo "Stripe rollout flags must be true or false."
		exit 1
	fi
done
if [[ "$STRIPE_BILLING_ENABLED" == "true" && "$STRIPE_INTEGRATION_ENABLED" != "true" ]]; then
	echo "Stripe acquisition cannot be enabled while integration maintenance is disabled."
	exit 1
fi
if [[ "$(echo "$ENV_JSON" | jq -r '.STRIPE_LIVE_MODE // empty')" != "false" ]]; then
	echo "Staging Stripe mode must remain explicitly false, even while disabled."
	exit 1
fi
if [[ "$STRIPE_INTEGRATION_ENABLED" == "true" ]]; then
	for var in STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET STRIPE_ACCOUNT_ID STRIPE_PRICE_ID STRIPE_API_VERSION; do
		if [[ -z "$(echo "$ENV_JSON" | jq -r ".\"$var\" // empty")" ]]; then
			echo "$var is required when Stripe integration is enabled."
			exit 1
		fi
	done
	if [[ "$(echo "$ENV_JSON" | jq -r '.STRIPE_SECRET_KEY // empty')" != sk_test_* && \
		"$(echo "$ENV_JSON" | jq -r '.STRIPE_SECRET_KEY // empty')" != rk_test_* ]]; then
		echo "Staging Stripe key must be a test-mode secret or restricted key."
		exit 1
	fi
	for spec in "STRIPE_WEBHOOK_SECRET:whsec_" "STRIPE_ACCOUNT_ID:acct_" "STRIPE_PRICE_ID:price_"; do
		var=${spec%%:*}
		prefix=${spec#*:}
		if [[ "$(echo "$ENV_JSON" | jq -r ".\"$var\" // empty")" != "$prefix"* ]]; then
			echo "$var has an invalid shape."
			exit 1
		fi
	done
fi

if ! RECALL_WEBHOOK_SECRET_INPUT=$(printf '%s' "$ENV_JSON" |
	jq -er '.RECALL_WEBHOOK_SECRET | select(type == "string")'); then
	echo "Staging Recall webhook signing credential is invalid."
	exit 1
fi
if ! RECALL_WEBHOOK_SECRET="$RECALL_WEBHOOK_SECRET_INPUT" \
	python "$SCRIPT_DIR/../infra/api_only_secret_live_guard.py" \
	--validate-signing-secret-only; then
	echo "Staging Recall webhook signing credential is invalid."
	exit 1
fi
unset RECALL_WEBHOOK_SECRET_INPUT

DURABLE_WEBHOOK_INBOX_ENABLED=$(
	echo "$ENV_JSON" | jq -r '.DURABLE_WEBHOOK_INBOX_ENABLED // "false"'
)
if [[ "$DURABLE_WEBHOOK_INBOX_ENABLED" != "true" && \
	"$DURABLE_WEBHOOK_INBOX_ENABLED" != "false" ]]; then
	echo "DURABLE_WEBHOOK_INBOX_ENABLED must be true or false."
	exit 1
fi

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

BILLING_SECRET_VARS=(
	STRIPE_SECRET_KEY
)

BILLING_API_ONLY_SECRET_VARS=(
	STRIPE_WEBHOOK_SECRET
)

BILLING_PUBLIC_VARS=(
	STRIPE_INTEGRATION_ENABLED STRIPE_BILLING_ENABLED STRIPE_ACCOUNT_ID
	STRIPE_PRICE_ID STRIPE_API_VERSION STRIPE_LIVE_MODE
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
	AGENT_API_ENABLED AGENT_API_AUDIENCE AGENT_API_SOVEREIGN_CLIENT_IDS
	AUTHORITY_KERNEL_ENABLED AUTHORITY_CLERK_ISSUER MOUNT_CONTEXT_TOKEN_TTL_SECONDS
	MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS
	DURABLE_WEBHOOK_INBOX_ENABLED
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
	secret_env_names=$(IFS=,; echo "${COMMON_SECRET_VARS[*]}")
	public_env_names=$(IFS=,; echo "${PUBLIC_VARS[*]}")
	if ! printf '%s' "$migration_template" |
		python "$SCRIPT_DIR/../scripts/migration_job_template_guard.py" \
			--expected-name "$MIGRATION_JOB" \
			--expected-image "$STAGING_TAG" \
			--database-secret-name database-url \
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

	# A plain job start executes this persistent template. Require the exact
	# reviewed contract before the read-only preflight and again immediately
	# before schema mutation so an intervening out-of-band edit fails closed.
	verify_migration_job_template || return 1

	# Run the aggregate-only gate as an execution override. Azure replaces the
	# container definition when an override is supplied, so explicitly restore
	# the DATABASE_URL secret reference. The job template remains Alembic-owned,
	# so a successful preflight is followed by a plain start while any failure
	# leaves the schema untouched.
	local preflight_execution
	if ! preflight_execution=$(az containerapp job start \
		-n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
		--container-name "$migration_container_name" \
		--image "$STAGING_TAG" \
		--command /opt/venv/bin/python \
		--args /app/scripts/billing_migration_preflight.py \
		--env-vars "DATABASE_URL=secretref:database-url" \
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

record_grants_enabled_for() {
	local container_name=$1
	[[ "$container_name" == "$API_CONTAINER" && \
		"$ENABLE_SOVEREIGN_RECORD_GRANTS" == "true" ]]
}

billing_workload_for() {
	local container_name=$1
	[[ "$container_name" == "$API_CONTAINER" || "$container_name" == "$WORKER_CONTAINER" ]]
}

billing_integration_enabled_for() {
	local container_name=$1
	billing_workload_for "$container_name" && [[ "$STRIPE_INTEGRATION_ENABLED" == "true" ]]
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
	local container_public_vars=("${PUBLIC_VARS[@]}")
	if [[ "$container_name" == "$API_CONTAINER" ]]; then
		container_secret_vars+=("${API_ONLY_SECRET_VARS[@]}")
	fi
	if record_grants_enabled_for "$container_name"; then
		container_secret_vars+=(SOVEREIGN_RECORD_GRANT_SECRET)
	fi
	if billing_workload_for "$container_name"; then
		container_public_vars+=("${BILLING_PUBLIC_VARS[@]}")
	fi
	if billing_integration_enabled_for "$container_name"; then
		container_secret_vars+=("${BILLING_SECRET_VARS[@]}")
		if [[ "$container_name" == "$API_CONTAINER" ]]; then
			container_secret_vars+=("${BILLING_API_ONLY_SECRET_VARS[@]}")
		fi
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

	for var in "${container_public_vars[@]}"; do
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
	if [[ "$container_name" == "$WORKER_CONTAINER" ]]; then
		update_args+=(--args "$STAGING_WORKER_COMMAND")
	fi
	if [[ "$container_name" == "$SCHEDULER_CONTAINER" ]]; then
		update_args+=(--args "$SCHEDULER_COMMAND")
	fi
	local remove_env_vars=()
	if ! record_grants_enabled_for "$container_name"; then
		remove_env_vars+=("$GRANT_SECRET_ENV_NAME")
	fi
	if [[ "$container_name" != "$API_CONTAINER" ]]; then
		remove_env_vars+=("$RECALL_SECRET_ENV_NAME")
	fi
	if ! billing_integration_enabled_for "$container_name"; then
		remove_env_vars+=(STRIPE_SECRET_KEY)
	fi
	if [[ "$container_name" != "$API_CONTAINER" || "$STRIPE_INTEGRATION_ENABLED" != "true" ]]; then
		remove_env_vars+=(STRIPE_WEBHOOK_SECRET)
	fi
	if ! billing_workload_for "$container_name"; then
		remove_env_vars+=("${BILLING_PUBLIC_VARS[@]}")
	fi
	if [[ ${#remove_env_vars[@]} -gt 0 ]]; then
		update_args+=(--remove-env-vars "${remove_env_vars[@]}")
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

remove_unowned_billing_secrets() {
	local container_name=$1
	local remove_names=()
	if ! billing_integration_enabled_for "$container_name"; then
		remove_names+=(stripe-secret-key)
	fi
	if [[ "$container_name" != "$API_CONTAINER" || "$STRIPE_INTEGRATION_ENABLED" != "true" ]]; then
		remove_names+=(stripe-webhook-secret)
	fi
	for secret_name in "${remove_names[@]}"; do
		local secret_count
		secret_count=$(az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" \
			--query "properties.configuration.secrets[?name=='$secret_name'] | length(@)" \
			-o tsv) || return 1
		if [[ "$secret_count" != "0" ]]; then
			az containerapp secret remove -n "$container_name" -g "$RESOURCE_GROUP" \
				--secret-names "$secret_name" || return 1
		fi
	done
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
	# Optional: auxiliary containers pass a smaller budget. Waiting the full five
	# minutes on a container that cannot fail the deploy only slows every deploy.
	local max_attempts=${3:-60} # default 60 * 5s = 5 minutes
	local expected_image="${STAGING_IMAGE}:${git_sha}"
	local expected_revision_name="${container_name}--${REVISION_STRING}"

	# Initialize backoff variables
	local attempts=0

	echo "Checking deployment status for $container_name..."

	while [[ $attempts -lt $max_attempts ]]; do
		# Revision-list order is unspecified. Inspect the exact latest revision
		# created by the update rather than accepting an arbitrary healthy image.
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

		if [[ "$current_image" != "$expected_image" ]]; then
			echo "  $container_name: latest revision still $current_image, waiting for $expected_image (attempt $((attempts + 1))/$max_attempts)"
			sleep 5
			((attempts++))
			continue
		fi

		if [[ "$provisioning_state" == "Failed" || "$running_state" == "Failed" ]]; then
			echo "$container_name: revision $latest_rev_name failed (provisioning=$provisioning_state, running=$running_state, health=$health_state)"
			return 1
		fi

		# Healthy is the readiness signal. runningState may be Running for the
		# API or RunningAtMaxScale for a worker, so do not pin one spelling.
		if [[ "$health_state" == "Healthy" && "$provisioning_state" == "Provisioned" ]]; then
			if [[ "$container_name" == "$SCHEDULER_CONTAINER" ]]; then
				local live_scheduler_command
				live_scheduler_command=$(az containerapp revision show -n "$container_name" -g "$RESOURCE_GROUP" \
					--revision "$latest_rev_name" \
					--query "properties.template.containers[0].args[0]" -o tsv)
				if [[ "$live_scheduler_command" != "$SCHEDULER_COMMAND" ]]; then
					echo "Scheduler command does not match the checked-in Procfile."
					return 1
				fi
			fi
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

# Deploy and verify recovery workloads before making the new API revision live.
for container in $BACKEND_CONTAINERS; do
	initiate_deployment "$container" "$GIT_SHA"
	if [[ $? -ne 0 ]]; then
		echo "Deployment initiation failed for $container. Exiting."
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

for container in $CRITICAL_CONTAINERS; do
	remove_unowned_billing_secrets "$container"
	if [[ $? -ne 0 ]]; then
		echo "Failed to reconcile Stripe secret scope for $container."
		exit 1
	fi
done

# A false gate is a real revocation, not merely a refusal to add the key on the
# next revision. Remove any stale app-level secret after the new revisions no
# longer reference it.
for container in $CRITICAL_CONTAINERS; do
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

echo "All critical deployments are successful for Git SHA $GIT_SHA."
exit 0
