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

# TODO(mbianco) these should really be pulled from the ENV or somewhere more stable
# Define variables
RESOURCE_GROUP="cosmaneura-prod"
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
