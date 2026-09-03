#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM_GROUP="cosmaneura-staging"
PLATFORM_APP="cosmaneura-staging-app"
EXPECTED_PLATFORM_ORIGIN="https://staging-app.cosmaneura.com"
EXPECTED_PLATFORM_IMAGE_DIGEST="${EXPECTED_PLATFORM_IMAGE_DIGEST:-}"
SOVEREIGN_GROUP="cosmaneura-sovereign-staging"
PROVIDER_APP="cosmaneura-sovereign-staging-app"
PUBLIC_APP="cosmaneura-web-staging-app"
EXPECTED_PUBLIC_CLIENT_ID="5074cac1-5555-43e9-a469-3e0b7e16608e"

fail() {
	echo "V7_SMOKE FAIL"
	exit 1
}

[[ "$EXPECTED_PLATFORM_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail

fqdn() {
	az containerapp show --resource-group "$1" --name "$2" \
		--query properties.configuration.ingress.fqdn --output tsv
}

PLATFORM_FQDN="$(fqdn "$PLATFORM_GROUP" "$PLATFORM_APP")"
PROVIDER_FQDN="$(fqdn "$SOVEREIGN_GROUP" "$PROVIDER_APP")"
PUBLIC_FQDN="$(fqdn "$SOVEREIGN_GROUP" "$PUBLIC_APP")"
PUBLIC_IDENTITY_TYPE="$(az containerapp show --resource-group "$SOVEREIGN_GROUP" --name "$PUBLIC_APP" \
	--query identity.type --output tsv)"
PUBLIC_IDENTITY_COUNT="$(az containerapp show --resource-group "$SOVEREIGN_GROUP" --name "$PUBLIC_APP" \
	--query 'length(keys(identity.userAssignedIdentities))' --output tsv)"
PUBLIC_CLIENT_ID="$(az containerapp show --resource-group "$SOVEREIGN_GROUP" --name "$PUBLIC_APP" \
	--query 'values(identity.userAssignedIdentities)[0].clientId' --output tsv)"
SCOPE="$(az containerapp show --resource-group "$SOVEREIGN_GROUP" --name "$PROVIDER_APP" \
	--query "properties.template.containers[0].env[?name=='PLATFORM_AGENT_API_SCOPE'].value | [0]" --output tsv)"
PLATFORM_STATE="$(az containerapp show --resource-group "$PLATFORM_GROUP" --name "$PLATFORM_APP" \
	--query '{latestRevision:properties.latestRevisionName,latestReadyRevision:properties.latestReadyRevisionName,activeRevisionsMode:properties.configuration.activeRevisionsMode,traffic:properties.configuration.ingress.traffic}' --output json)"
if ! PLATFORM_READY_REVISION=$(jq -er '
	select(.latestRevision == .latestReadyRevision and .activeRevisionsMode == "Single") |
	.latestReadyRevision as $ready |
	[.traffic[]? | select((.weight // 0) > 0)] as $routes |
	select(($routes | length) == 1 and $routes[0].weight == 100 and
	 ($routes[0].latestRevision == true or $routes[0].revisionName == $ready)) |
	$ready | select(type == "string" and length > 0)
' <<<"$PLATFORM_STATE"); then
	fail
fi
PLATFORM_REVISION="$(az containerapp revision show \
	--resource-group "$PLATFORM_GROUP" --name "$PLATFORM_APP" \
	--revision "$PLATFORM_READY_REVISION" \
	--query "{containers:properties.template.containers[].{name:name,image:image,env:env[?name=='API_BASE_URL'].{name:name,value:value,secretRef:secretRef}},health:properties.healthState,running:properties.runningState,provisioning:properties.provisioningState}" \
	--output json)"
if ! PLATFORM_CONTRACT=$(jq -cer --arg app "$PLATFORM_APP" '
	select((.containers | type) == "array" and (.containers | length) == 1 and
	 .containers[0].name == $app and .health == "Healthy" and
	 .provisioning == "Provisioned" and
	 (.running == "Running" or .running == "RunningAtMaxScale")) |
	.containers[0] as $container |
	[$container.env[]? | select(.name == "API_BASE_URL" and
	 (.value | type == "string") and .secretRef == null)] as $origins |
	select(($origins | length) == 1) |
	{image: $container.image, origin: $origins[0].value}
' <<<"$PLATFORM_REVISION"); then
	fail
fi
PLATFORM_ORIGIN=$(jq -r '.origin' <<<"$PLATFORM_CONTRACT")
PLATFORM_IMAGE=$(jq -r '.image' <<<"$PLATFORM_CONTRACT")

for value in "$PLATFORM_FQDN" "$PROVIDER_FQDN" "$PUBLIC_FQDN"; do
	[[ "$value" =~ ^[a-z0-9.-]+$ ]] || fail
done
[[ "$PLATFORM_ORIGIN" =~ ^https://[a-z0-9.-]+$ ]] || fail
[[ "$PLATFORM_ORIGIN" == "$EXPECTED_PLATFORM_ORIGIN" ]] || fail
[[ "$PUBLIC_IDENTITY_TYPE" == "UserAssigned" && "$PUBLIC_IDENTITY_COUNT" == "1" ]] || fail
[[ "$PUBLIC_CLIENT_ID" == "$EXPECTED_PUBLIC_CLIENT_ID" ]] || fail
[[ "$SCOPE" =~ ^api://[0-9a-f-]{36}/\.default$ ]] || fail
[[ "$PLATFORM_IMAGE" == "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@$EXPECTED_PLATFORM_IMAGE_DIGEST" ]] || fail

run_probe() {
	local group="$1" app="$2" command="$3" marker="$4"
	/usr/bin/expect "$ROOT/infra-staging/containerapp-exec.expect" \
		"$group" "$app" "$command" "$marker" >/dev/null 2>&1 || {
		echo "V7_SMOKE FAIL"
		return 1
	}
}

provider_command="PYTHONPATH=/app /opt/venv/bin/python /app/infra-staging/provider_v7_smoke.py"
for argument in "$PLATFORM_ORIGIN" "https://$PROVIDER_FQDN" "https://$PUBLIC_FQDN"; do
	provider_command+=" '$argument'"
done
run_probe "$PLATFORM_GROUP" "$PLATFORM_APP" "$provider_command" "V7_PROVIDER_SMOKE PASS"

payload="$(python3 -c 'import base64,sys,zlib;print(base64.b64encode(zlib.compress(open(sys.argv[1],"rb").read(),9)).decode())' "$ROOT/infra-staging/provider_v7_public_probe.py")"
public_command="/opt/venv/bin/python -c \"import base64,zlib;exec(zlib.decompress(base64.b64decode('$payload')))\""
for argument in "$PLATFORM_ORIGIN" "$SCOPE" "$PUBLIC_CLIENT_ID"; do
	public_command+=" '$argument'"
done
[[ ${#public_command} -lt 1000 ]] || fail
run_probe "$SOVEREIGN_GROUP" "$PUBLIC_APP" "$public_command" "V7_PUBLIC_DENIAL PASS"

echo "V7_SMOKE PASS"
