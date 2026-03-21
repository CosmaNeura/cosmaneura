#!/usr/bin/env bash

# Description: deploy already-built containers to staging

set -euo pipefail

GIT_SHA="$1"

RESOURCE_GROUP="cosmaneura-staging"
CONTAINERS="cosmaneura-staging-app cosmaneura-staging-worker cosmaneura-staging-app-jmon"
STAGING_IMAGE="cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura"

if [[ -z "$GIT_SHA" ]]; then
  echo "Error: Please provide a Git SHA as an argument."
  exit 1
fi

if ! git rev-parse --quiet --verify "$GIT_SHA^{commit}" >/dev/null; then
  echo "Error: $GIT_SHA is not a valid commit in this repository."
  exit 1
fi

echo "Azure Account Information:"
az account show

echo "All Available Staging Containers:"
az containerapp list -g "$RESOURCE_GROUP" --query "[].name"

GIT_SHA=$(git rev-parse "$GIT_SHA")
STAGING_TAG="$STAGING_IMAGE:$GIT_SHA"

if ! docker manifest inspect "$STAGING_TAG" >/dev/null 2>&1; then
  echo "Error: Image $STAGING_TAG does not exist"
  exit 1
fi

gen_string_for_sha() {
  local sha="$1"
  local sys_date count short_sha

  sys_date=$(date +%Y-%m-%d)
  count=$(git rev-list --count "$sha")
  short_sha=$(echo "$sha" | cut -c1-7)

  echo "${sys_date}-v${count}-${short_sha}"
}

REVISION_STRING=$(gen_string_for_sha "$GIT_SHA")

echo "Revision string: $REVISION_STRING"

initiate_deployment() {
  local container_name=$1
  local git_sha=$2
  local image="${STAGING_IMAGE}:${git_sha}"

  echo "Initiating deployment for $container_name with image $image..."
  az containerapp update -n "$container_name" -g "$RESOURCE_GROUP" -i "$image" --revision-suffix "$REVISION_STRING"
}

check_deployment() {
  local container_name=$1
  local git_sha=$2
  local expected_image="${STAGING_IMAGE}:${git_sha}"

  local attempts=0
  local max_attempts=60

  echo "Checking deployment status for $container_name..."

  while [[ $attempts -lt $max_attempts ]]; do
    provisioning_state=$(az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" --query "properties.provisioningState" -o tsv)
    current_image=$(az containerapp revision list -n "$container_name" -g "$RESOURCE_GROUP" --query "[0].properties.template.containers[0].image" -o tsv)
    running_state=$(az containerapp show -n "$container_name" -g "$RESOURCE_GROUP" --query "properties.runningStatus" -o tsv)

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

for container in $CONTAINERS; do
  initiate_deployment "$container" "$GIT_SHA"
done

for container in $CONTAINERS; do
  check_deployment "$container" "$GIT_SHA"
done

echo "All staging deployments are successful for Git SHA $GIT_SHA."
