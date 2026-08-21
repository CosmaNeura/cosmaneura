#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM_GROUP="cosmaneura-staging"
PLATFORM_APP="cosmaneura-staging-app"
EXPECTED_PLATFORM_ORIGIN="https://staging-app.cosmaneura.com"
SOVEREIGN_GROUP="cosmaneura-sovereign-staging"
PROVIDER_APP="cosmaneura-sovereign-staging-app"
PUBLIC_APP="cosmaneura-web-staging-app"

fqdn() {
	az containerapp show --resource-group "$1" --name "$2" \
		--query properties.configuration.ingress.fqdn --output tsv
}

PLATFORM_FQDN="$(fqdn "$PLATFORM_GROUP" "$PLATFORM_APP")"
PROVIDER_FQDN="$(fqdn "$SOVEREIGN_GROUP" "$PROVIDER_APP")"
PUBLIC_FQDN="$(fqdn "$SOVEREIGN_GROUP" "$PUBLIC_APP")"
PLATFORM_ORIGIN="$(az containerapp show --resource-group "$PLATFORM_GROUP" --name "$PLATFORM_APP" \
	--query "properties.template.containers[0].env[?name=='API_BASE_URL'].value | [0]" --output tsv)"
SCOPE="$(az containerapp show --resource-group "$SOVEREIGN_GROUP" --name "$PROVIDER_APP" \
	--query "properties.template.containers[0].env[?name=='PLATFORM_AGENT_API_SCOPE'].value | [0]" --output tsv)"
HEAD="$(git -C "$ROOT" rev-parse HEAD)"
PLATFORM_IMAGE="$(az containerapp show --resource-group "$PLATFORM_GROUP" --name "$PLATFORM_APP" \
	--query properties.template.containers[0].image --output tsv)"

for value in "$PLATFORM_FQDN" "$PROVIDER_FQDN" "$PUBLIC_FQDN"; do
	[[ "$value" =~ ^[a-z0-9.-]+$ ]] || { echo "V7_SMOKE FAIL"; exit 1; }
done
[[ "$PLATFORM_ORIGIN" =~ ^https://[a-z0-9.-]+$ ]] || { echo "V7_SMOKE FAIL"; exit 1; }
[[ "$PLATFORM_ORIGIN" == "$EXPECTED_PLATFORM_ORIGIN" ]] || { echo "V7_SMOKE FAIL"; exit 1; }
[[ "$SCOPE" =~ ^api://[0-9a-f-]{36}/\.default$ ]] || { echo "V7_SMOKE FAIL"; exit 1; }
[[ "$PLATFORM_IMAGE" == "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura:$HEAD" ]] || { echo "V7_SMOKE FAIL"; exit 1; }

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
for argument in "$PLATFORM_ORIGIN" "$SCOPE"; do
	public_command+=" '$argument'"
done
[[ ${#public_command} -lt 1000 ]] || { echo "V7_SMOKE FAIL"; exit 1; }
run_probe "$SOVEREIGN_GROUP" "$PUBLIC_APP" "$public_command" "V7_PUBLIC_DENIAL PASS"

echo "V7_SMOKE PASS"
