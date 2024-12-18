#!/usr/bin/env zsh

GIT_SHA="$1"

# Define variables
RESOURCE_GROUP="cosmaneura-prod"
CONTAINERS="cosmaneura-prod-app cosmaneura-prod-worker"
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
REVISION_STRING=$(gen_string_for_sha "$GIT_SHA")

echo "Revision string: $REVISION_STRING"

initiate_deployment() {
	local container_name=$1
	local git_sha=$2
	local image="${PRODUCTION_IMAGE}:${git_sha}"

	echo "Initiating deployment for $container_name with image $image..."
	az containerapp update -n "$container_name" -g "$RESOURCE_GROUP" -i "$image"
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
		health_state=$(az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" --query "properties.configuration.activeRevisions[0].properties.healthState" -o tsv)

		# Validate all conditions
		if [[ "$provisioning_state" == "Succeeded" && "$current_image" == "$expected_image" && "$health_state" == "Healthy" ]]; then
			echo "$container_name deployment is successful."
			return 0
		fi

		echo "Retrying in 5 seconds... (Attempt $((attempts + 1))/$max_attempts)"
		sleep 5
		((attempts++))
	done

	echo "Deployment check failed for $container_name after $max_attempts attempts."
	return 1
}

# Deploy all containers first
overall_status=0
for container in $CONTAINERS; do
	initiate_deployment "$container" "$GIT_SHA"
	if [[ $? -ne 0 ]]; then
		overall_status=1
	fi
done

# Exit early if deployment initiation failed for any container
if [[ $overall_status -ne 0 ]]; then
	echo "One or more deployments failed to initiate. Aborting status checks."
	exit 1
fi

# Check deployment status for all containers
overall_status=0
for container in $CONTAINERS; do
	check_deployment "$container" "$GIT_SHA"
	if [[ $? -ne 0 ]]; then
		overall_status=1
	fi
done

# Final result
if [[ $overall_status -eq 0 ]]; then
	echo "All deployments are successful for Git SHA $GIT_SHA."
	exit 0
else
	echo "One or more deployments failed for Git SHA $GIT_SHA."
	exit 1
fi
