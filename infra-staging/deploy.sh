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

GIT_SHA="${1:-}"
STAGING_IMAGE_DIGEST="${2:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../scripts/deploy_revision_suffix.sh"

EXPECTED_SCHEMA_BEFORE="${EXPECTED_SCHEMA_BEFORE:-}"
EXPECTED_SCHEMA_AFTER="${EXPECTED_SCHEMA_AFTER:-}"
EXPECTED_RECOVERY_DIGEST="${EXPECTED_RECOVERY_DIGEST:-}"
EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256="${EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256:-}"
STAGING_DEPLOYMENT_IDENTITY_MODE="${STAGING_DEPLOYMENT_IDENTITY_MODE:-service-principal}"
STAGING_APPROVAL_PR_NUMBER="${STAGING_APPROVAL_PR_NUMBER:-}"

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
STAGING_REPOSITORY="cosmaneura/cosmaneura"
STAGING_IMAGE_REFERENCE="$STAGING_IMAGE@$STAGING_IMAGE_DIGEST"
PINNED_STAGING_CLIENT_ID="45b940bf-2f4f-4b10-bcc3-4679d8855a6f"
PINNED_STAGING_TENANT_ID="d51754d0-bba5-4810-820e-afac542844d6"
PINNED_STAGING_SUBSCRIPTION_ID="bab2a709-1fcb-4c87-a9fc-802f7477ec13"
PINNED_FOUNDER_GITHUB_IDENTITY_SHA256="a4deb86ff0cd6ba7c4e14747a1f39526160ad161ae2ad2a4bb170d8d5d5d80f5"
PINNED_FOUNDER_AZURE_USERNAME_SHA256="acf2c107f43fa6950918e5f089e1b865ba56188295b88d3d72ad342d742e616a"
PINNED_FOUNDER_AZURE_OBJECT_ID_SHA256="0839f64960f7e378762f2f1aab2d324b197c4c0d2198f87d9f595df4eb0b1f93"

hash_stdin() {
	python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}

canonical_github_user_sha256() {
	python3 -c '
import hashlib
import json
import sys

def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate identity field")
        value[key] = item
    return value

try:
    user = json.load(sys.stdin, object_pairs_hook=unique)
    if (
        not isinstance(user, dict)
        or user.get("login") != "JohnClem"
        or user.get("type") != "User"
        or type(user.get("id")) is not int
        or user["id"] <= 0
    ):
        raise ValueError("invalid founder identity")
    identity = {key: user[key] for key in ("id", "login", "type")}
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
print(hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
'
}

verify_founder_release_source() {
	[[ "$STAGING_DEPLOYMENT_IDENTITY_MODE" == "founder-user" ]] || return 0
	local founder_identity_json founder_identity_sha256 controller_sha canonical_origin dirty_checkout
	if [[ ! "$STAGING_APPROVAL_PR_NUMBER" =~ ^[1-9][0-9]*$ ]]; then
		echo "Founder-user staging deployment requires an approval PR number."
		return 1
	fi
	if ! canonical_origin=$(git remote get-url origin) || \
		! dirty_checkout=$(git status --porcelain=v1 --untracked-files=all); then
		echo "Failed to inspect the local staging controller repository."
		return 1
	fi
	if [[ "$canonical_origin" != "https://github.com/CosmaNeura/cosmaneura.git" || \
		-n "$dirty_checkout" ]]; then
		echo "Founder-user staging deployment requires the clean canonical repository."
		return 1
	fi
	if ! founder_identity_json=$(gh api --hostname github.com --method GET user); then
		echo "Failed to authenticate the local GitHub operator."
		return 1
	fi
	if ! founder_identity_sha256=$(printf '%s' "$founder_identity_json" | canonical_github_user_sha256); then
		echo "Failed to fingerprint the local GitHub operator."
		return 1
	fi
	unset founder_identity_json
	if [[ "$founder_identity_sha256" != "$PINNED_FOUNDER_GITHUB_IDENTITY_SHA256" ]]; then
		echo "The local GitHub operator is not the pinned founder."
		return 1
	fi
	if ! controller_sha=$(git rev-parse HEAD); then
		echo "Failed to resolve the local staging controller."
		return 1
	fi
	if ! python3 "$SCRIPT_DIR/../scripts/verify_staging_candidate.py" \
		--candidate-sha "$GIT_SHA" \
		--approval-pr-number "$STAGING_APPROVAL_PR_NUMBER" \
		--expected-schema-after "$EXPECTED_SCHEMA_AFTER" \
		--workflow-sha "$controller_sha" \
		--repository CosmaNeura/cosmaneura \
		--event-name workflow_dispatch \
		--ref refs/heads/master \
		--ref-protected true \
		--actor JohnClem \
		--triggering-actor JohnClem >/dev/null; then
		echo "Founder-user staging candidate provenance verification failed."
		return 1
	fi
}

case "$STAGING_DEPLOYMENT_IDENTITY_MODE" in
service-principal) ;;
founder-user) verify_founder_release_source || exit 1 ;;
*) echo "Unsupported staging deployment identity mode."; exit 1 ;;
esac

# Preserve explicit deployment controls before direnv renders the secret
# bundle. `direnv export` emits a delta, so values inherited unchanged by the
# rendered environment may be absent from its JSON output.
ENABLE_PROVIDER_V7="${ENABLE_PROVIDER_V7:-}"
ENABLE_SOVEREIGN_RECORD_GRANTS="${ENABLE_SOVEREIGN_RECORD_GRANTS:-}"

# The canonical catch-up preserves these already-enabled staging capabilities.
# Disabling either one is a separate reviewed incident/control change, not a
# side effect permitted through this deployment executor.
if [[ "$ENABLE_PROVIDER_V7" != "true" ]]; then
	echo "ENABLE_PROVIDER_V7 must remain true for canonical staging deployment."
	exit 1
fi
if [[ "$ENABLE_SOVEREIGN_RECORD_GRANTS" != "true" ]]; then
	echo "ENABLE_SOVEREIGN_RECORD_GRANTS must remain true for canonical staging deployment."
	exit 1
fi

# Resolve protected configuration before invoking Git, Docker, Azure, or any
# other provider-facing subprocess. Keep the resolved JSON in this shell only.
# The resolver is the only child allowed to inherit the ambient capabilities;
# drop them immediately on either success or failure. ENV_JSON is deliberately
# not exported, so later child processes receive only explicitly scoped values.
CONFIGURATION_STATUS=0
ENV_JSON=$(cd "$SCRIPT_DIR/.." && just direnv_export .env.staging.backend) || \
	CONFIGURATION_STATUS=$?
unset OP_SERVICE_ACCOUNT_TOKEN RECALL_WEBHOOK_SECRET
if [[ "$CONFIGURATION_STATUS" -ne 0 ]]; then
	echo "Failed to load staging deployment configuration."
	exit 1
fi
if ! printf '%s' "$ENV_JSON" |
	python "$SCRIPT_DIR/../scripts/superadmin_capability_config_preflight.py" \
		--environment staging; then
	echo "Staging capability identity configuration is not deployable."
	exit 1
fi

# Canonical staging deploys accept only a full source SHA plus an immutable
# registry digest. Validate the untrusted inputs only after the ambient
# provider capabilities have been removed.
if [[ ! "$GIT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
	echo "Error: staging source must be a full lowercase 40-character Git SHA."
	exit 1
fi
if [[ ! "$STAGING_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
	echo "Error: staging image digest must be sha256 followed by 64 lowercase hex characters."
	exit 1
fi
for schema_revision in "$EXPECTED_SCHEMA_BEFORE" "$EXPECTED_SCHEMA_AFTER"; do
	if [[ ! "$schema_revision" =~ ^[0-9a-f]{12}$ ]]; then
		echo "Expected schema revisions must be 12 lowercase hex characters."
		exit 1
	fi
done
if [[ ! "$EXPECTED_RECOVERY_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
	echo "Expected recovery digest must be sha256 followed by 64 lowercase hex characters."
	exit 1
fi
if [[ ! "$EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256" =~ ^sha256:[0-9a-f]{64}$ ]]; then
	echo "Expected staging identity fingerprint-set hash must use the canonical sha256 form."
	exit 1
fi
if ! RESOLVED_GIT_SHA=$(git rev-parse --quiet --verify "$GIT_SHA^{commit}"); then
	echo "Error: staging source is not a valid commit in this repository."
	exit 1
fi
if [[ "$RESOLVED_GIT_SHA" != "$GIT_SHA" ]]; then
	echo "Error: staging source did not resolve to the exact supplied commit."
	exit 1
fi
if ! CANDIDATE_PROCFILE=$(git show "$GIT_SHA:Procfile"); then
	echo "Error: the staging candidate does not contain a readable Procfile."
	exit 1
fi
if ! STAGING_WORKER_COMMAND=$(printf '%s' "$CANDIDATE_PROCFILE" | yq -er '.worker'); then
	echo "Error: the staging candidate Procfile worker command is missing."
	exit 1
fi
if ! SCHEDULER_COMMAND=$(printf '%s' "$CANDIDATE_PROCFILE" | yq -er '.scheduler'); then
	echo "Error: the staging candidate Procfile scheduler command is missing."
	exit 1
fi
unset CANDIDATE_PROCFILE RESOLVED_GIT_SHA

# Keep the provider-v7 runtime gates synchronized even when the inherited
# control was omitted from direnv's delta.
if ! ENV_JSON=$(echo "$ENV_JSON" | jq -c \
	--arg enabled "$ENABLE_PROVIDER_V7" \
	'. + {
		AUTHORITY_KERNEL_ENABLED: $enabled,
		AGENT_API_ENABLED: $enabled,
		SCRIBE_HANDOFF_ENABLED: "false",
		SCRIBE_WORKFLOW_ENABLED: "false",
		SESSION_PREP_SYNTHETIC_ENABLED: "false",
		STRIPE_INTEGRATION_ENABLED: "false",
		STRIPE_BILLING_ENABLED: "false",
		STRIPE_LIVE_MODE: "false",
		DURABLE_WEBHOOK_INBOX_ENABLED: "false",
		SENTRY_STAGING_ENABLED: "false",
		ZOOM_WATCHDOG_ENABLED: "false"
	}'); then
	echo "Failed to reconcile provider V7 staging configuration."
	exit 1
fi
if ! jq -e '
	.SOVEREIGN_RECORD_GRANT_SECRET |
	type == "string" and length > 0
' <<<"$ENV_JSON" >/dev/null; then
	echo "Staging record grants were enabled without a configured key."
	exit 1
fi

emit_github_output() {
	local name=$1 value=$2
	case "$name" in
	source_sha | image_digest | configuration_evidence | schema_before | schema_after | recovery_digest | workloads_before | workloads_after) ;;
	*) echo "Refusing to emit an output outside the staging evidence allowlist."; return 1 ;;
	esac
	[[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || {
		echo "Refusing to emit multiline staging evidence."; return 1;
	}
	[[ -z "${GITHUB_OUTPUT:-}" ]] || printf '%s=%s\n' "$name" "$value" >>"$GITHUB_OUTPUT"
}

build_configuration_evidence() {
	python3 -c '
import hashlib, json, sys
from urllib.parse import parse_qs, urlsplit
def fail():
    raise SystemExit("Staging configuration evidence validation failed.")
try:
    c = json.load(sys.stdin)
except (TypeError, ValueError):
    fail()
if not isinstance(c, dict):
    fail()
urls = dict(API_BASE_URL="https://staging-app.cosmaneura.com",
    ZOOM_REDIRECT_URI="https://staging-app.cosmaneura.com/external/v1/zoom-oauth-callback",
    ZOOM_SUCCESS_REDIRECT="https://staging-app.cosmaneura.com/settings",
    COOKIE_DOMAIN="staging-app.cosmaneura.com", RECALL_API_BASE="https://us-west-2.recall.ai/api/v1",
    ALLOWED_HOST_LIST="staging-app.cosmaneura.com,staging-chat.cosmaneura.com",
    INTAKE_CHAT_DOMAIN="staging-chat.cosmaneura.com", AUTHORITY_CLERK_ISSUER="https://resolved-emu-53.clerk.accounts.dev")
if c.get("PYTHON_ENV") != "staging" or any(c.get(k) != v for k, v in urls.items()):
    fail()
flag_names = "SCRIBE_HANDOFF_ENABLED SCRIBE_WORKFLOW_ENABLED SESSION_PREP_SYNTHETIC_ENABLED STRIPE_INTEGRATION_ENABLED STRIPE_BILLING_ENABLED STRIPE_LIVE_MODE DURABLE_WEBHOOK_INBOX_ENABLED ZOOM_WATCHDOG_ENABLED AGENT_API_ENABLED AUTHORITY_KERNEL_ENABLED SENTRY_STAGING_ENABLED".split()
flags = {k: c.get(k) for k in flag_names}
forced_false = "SCRIBE_HANDOFF_ENABLED SCRIBE_WORKFLOW_ENABLED SESSION_PREP_SYNTHETIC_ENABLED STRIPE_INTEGRATION_ENABLED STRIPE_BILLING_ENABLED STRIPE_LIVE_MODE DURABLE_WEBHOOK_INBOX_ENABLED ZOOM_WATCHDOG_ENABLED SENTRY_STAGING_ENABLED".split()
if any(v not in {"true", "false"} for v in flags.values()) or any(flags[k] != "false" for k in forced_false) or (flags["STRIPE_BILLING_ENABLED"] == "true" and flags["STRIPE_INTEGRATION_ENABLED"] != "true"):
    fail()
try:
    database = urlsplit(c["DATABASE_URL"])
    ssl_modes = parse_qs(database.query, keep_blank_values=True).get("sslmode", [])
except (KeyError, TypeError, ValueError):
    fail()
database_name = database.path.removeprefix("/")
if database.scheme not in {"postgresql", "postgresql+psycopg"} or database.hostname != "cosmaneura-staging-postgres.postgres.database.azure.com" or database.port != 5432 or database_name != "staging" or len(ssl_modes) != 1 or ssl_modes[0] not in {"require", "verify-full"}:
    fail()
ssl_mode = ssl_modes[0]
clerk_publishable = c.get("CLERK_DEPLOYMENT_PUBLISHABLE_KEY")
clerk_private = c.get("CLERK_PRIVATE_KEY")
if not isinstance(clerk_publishable, str) or not isinstance(clerk_private, str) or not clerk_publishable.startswith("pk_test_") or not clerk_private.startswith("sk_test_"):
    fail()
for name, prefixes in {
    "STRIPE_ACCOUNT_ID": ("acct_",),
    "STRIPE_SECRET_KEY": ("sk_test_", "rk_test_"),
    "STRIPE_WEBHOOK_SECRET": ("whsec_",),
}.items():
    value = c.get(name)
    if value not in (None, "") and (not isinstance(value, str) or not value.startswith(prefixes)):
        fail()
secret_fields = dict(pair.split(":") for pair in "clerk_publishable:CLERK_DEPLOYMENT_PUBLISHABLE_KEY clerk_private:CLERK_PRIVATE_KEY zoom_client:ZOOM_CLIENT_ID zoom_secret:ZOOM_CLIENT_SECRET zoom_token:ZOOM_SECRET_TOKEN recall_api:RECALL_API_KEY recall_webhook:RECALL_WEBHOOK_SECRET stripe_account:STRIPE_ACCOUNT_ID stripe_key:STRIPE_SECRET_KEY stripe_webhook:STRIPE_WEBHOOK_SECRET".split())
fingerprints = {}
for label, name in secret_fields.items():
    value = c.get(name)
    required = not label.startswith("stripe_") or flags["STRIPE_INTEGRATION_ENABLED"] == "true"
    value = "" if value is None and not required else value
    if not isinstance(value, str) or (required and not value):
        fail()
    fingerprints[label + "_sha256"] = hashlib.sha256(value.encode()).hexdigest() if value else None
fingerprint_set_sha256 = "sha256:" + hashlib.sha256(json.dumps(fingerprints, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
expected_fingerprint_set_sha256 = sys.argv[1]
blockers = []
if ssl_mode != "verify-full":
    blockers.append("database_sslmode_not_verify_full")
if fingerprint_set_sha256 != expected_fingerprint_set_sha256:
    blockers.append("staging_identity_fingerprint_set_mismatch")
evidence = {
    "environment": c["PYTHON_ENV"], "urls": {k: c[k] for k in urls},
    "database": dict(host=database.hostname, port=database.port, name=database_name,
        sslmode=ssl_mode, hostname_verification=ssl_mode == "verify-full"),
    "flags": flags, "identity_fingerprints_sha256": fingerprints,
    "identity_fingerprint_set_sha256": fingerprint_set_sha256,
    "expected_identity_fingerprint_set_sha256": expected_fingerprint_set_sha256,
    "deployment_blockers": blockers,
}
print(json.dumps(evidence, separators=(",", ":"), sort_keys=True))
' "$EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256" <<<"$ENV_JSON"
}

if ! CONFIGURATION_EVIDENCE_JSON=$(build_configuration_evidence); then
	echo "Failed to build sanitized staging configuration evidence."
	exit 1
fi
emit_github_output configuration_evidence "$CONFIGURATION_EVIDENCE_JSON" || exit 1
if ! jq -e '.deployment_blockers | length == 0' \
	<<<"$CONFIGURATION_EVIDENCE_JSON" >/dev/null; then
	echo "Staging configuration has unresolved deployment blockers."
	exit 1
fi

STRIPE_INTEGRATION_ENABLED=$(jq -r '.flags.STRIPE_INTEGRATION_ENABLED' \
	<<<"$CONFIGURATION_EVIDENCE_JSON")
STRIPE_BILLING_ENABLED=$(jq -r '.flags.STRIPE_BILLING_ENABLED' \
	<<<"$CONFIGURATION_EVIDENCE_JSON")

# These owned-variable lists are shared by deployment and live-state
# attestation. An observed variable outside the applicable list is rejected.
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
API_ONLY_SECRET_VARS=(RECALL_WEBHOOK_SECRET)
BILLING_SECRET_VARS=(STRIPE_SECRET_KEY)
BILLING_API_ONLY_SECRET_VARS=(STRIPE_WEBHOOK_SECRET)
BILLING_PUBLIC_VARS=(
	STRIPE_INTEGRATION_ENABLED STRIPE_BILLING_ENABLED STRIPE_ACCOUNT_ID
	STRIPE_PRICE_ID STRIPE_API_VERSION STRIPE_LIVE_MODE
)
PUBLIC_VARS=(
	PYTHON_ENV
	LOG_LEVEL EMAIL_FROM_ADDRESS PORT ALLOWED_HOST_LIST INTAKE_CHAT_DOMAIN
	AZURE_OPENAI_ENDPOINT
	AZURE_UPLOADS_CONTAINER_NAME AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME
	ZOOM_REDIRECT_URI ZOOM_SUCCESS_REDIRECT ZOOM_WATCHDOG_ENABLED
	API_BASE_URL RECALL_API_BASE
	COOKIE_DOMAIN
	SCRIBE_HANDOFF_ENABLED ENTRA_TENANT_ID SCRIBE_API_AUDIENCE SCRIBE_CALLER_CLIENT_ID
	SCRIBE_RECONCILER_CALLER_CLIENT_ID
	SCRIBE_WORKFLOW_ENABLED SESSION_PREP_SYNTHETIC_ENABLED
	AGENT_API_ENABLED AGENT_API_AUDIENCE AGENT_API_SOVEREIGN_CLIENT_IDS
	AUTHORITY_KERNEL_ENABLED AUTHORITY_CLERK_ISSUER MOUNT_CONTEXT_TOKEN_TTL_SECONDS
	MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS
	DURABLE_WEBHOOK_INBOX_ENABLED SENTRY_STAGING_ENABLED
)

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

configure_workload_contract() {
	local container_name=$1
	container_secret_vars=("${COMMON_SECRET_VARS[@]}")
	container_public_vars=("${PUBLIC_VARS[@]}")
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
}

if ! REVISION_STRING=$(generate_deploy_revision_suffix "$GIT_SHA"); then
	exit 1
fi
if ! validate_deploy_revision_suffix "$REVISION_STRING" $CONTAINERS; then
	exit 1
fi

echo "Revision string: $REVISION_STRING"
emit_github_output source_sha "$GIT_SHA" || exit 1

verify_azure_deployment_identity() {
	local azure_account_json founder_azure_username founder_azure_username_sha256
	local founder_azure_object_id founder_azure_object_id_sha256
	if ! azure_account_json=$(az account show \
		--query '{subscriptionId:id,tenantId:tenantId,userName:user.name,userType:user.type}' \
		-o json); then
		echo "Failed to inspect the active Azure identity."
		return 1
	fi
	case "$STAGING_DEPLOYMENT_IDENTITY_MODE" in
	service-principal)
		if [[ "${AZURE_CLIENT_ID:-}" != "$PINNED_STAGING_CLIENT_ID" || \
			"${AZURE_TENANT_ID:-}" != "$PINNED_STAGING_TENANT_ID" || \
			"${AZURE_SUBSCRIPTION_ID:-}" != "$PINNED_STAGING_SUBSCRIPTION_ID" ]]; then
			echo "Configured Azure identity inputs are not the pinned staging identity."
			return 1
		fi
		if ! printf '%s' "$azure_account_json" | jq -e \
			--arg subscription "$PINNED_STAGING_SUBSCRIPTION_ID" \
			--arg tenant "$PINNED_STAGING_TENANT_ID" \
			--arg client "$PINNED_STAGING_CLIENT_ID" \
			'.subscriptionId == $subscription and .tenantId == $tenant and
			 .userType == "servicePrincipal" and .userName == $client' >/dev/null; then
			echo "Active Azure identity is not the exact staging deployment identity."
			return 1
		fi
		;;
	founder-user)
		if [[ -n "${AZURE_CLIENT_ID:-}" || -n "${AZURE_TENANT_ID:-}" || \
			-n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
			echo "Founder-user staging deployment refuses configured service-principal inputs."
			return 1
		fi
		if ! founder_azure_username=$(printf '%s' "$azure_account_json" | jq -er \
			--arg subscription "$PINNED_STAGING_SUBSCRIPTION_ID" \
			--arg tenant "$PINNED_STAGING_TENANT_ID" \
			'if .subscriptionId == $subscription and .tenantId == $tenant and
			 .userType == "user" and (.userName | type) == "string" and
			 (.userName | length) > 0 then .userName else error("identity mismatch") end'); then
			echo "Active Azure user is outside the pinned staging scope."
			return 1
		fi
		if ! founder_azure_username_sha256=$(printf '%s' "$founder_azure_username" | hash_stdin); then
			echo "Failed to fingerprint the active Azure user."
			return 1
		fi
		if [[ "$founder_azure_username_sha256" != "$PINNED_FOUNDER_AZURE_USERNAME_SHA256" ]]; then
			echo "The active Azure user is not the pinned founder."
			return 1
		fi
		if ! founder_azure_object_id=$(az ad signed-in-user show --query id --output tsv); then
			echo "Failed to authenticate the Azure directory user."
			return 1
		fi
		if [[ ! "$founder_azure_object_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
			echo "The Azure directory user identity is invalid."
			return 1
		fi
		if ! founder_azure_object_id_sha256=$(printf '%s' "$founder_azure_object_id" | hash_stdin); then
			echo "Failed to fingerprint the Azure directory user."
			return 1
		fi
		if [[ "$founder_azure_object_id_sha256" != "$PINNED_FOUNDER_AZURE_OBJECT_ID_SHA256" ]]; then
			echo "The Azure directory user is not the pinned founder."
			return 1
		fi
		;;
	*)
		echo "Unsupported staging deployment identity mode."
		return 1
		;;
	esac
	echo "Azure staging identity verified for $STAGING_DEPLOYMENT_IDENTITY_MODE mode."
}
verify_azure_deployment_identity || exit 1

ACR_NAME=$(echo "$STAGING_IMAGE" | cut -d'.' -f1)
if ! PUBLISHED_DIGEST=$(az acr manifest show-metadata \
	--registry "$ACR_NAME" --name "$STAGING_REPOSITORY:$GIT_SHA" \
	--query digest -o tsv); then
	echo "Failed to resolve the staging candidate image digest."
	exit 1
fi
if [[ "$PUBLISHED_DIGEST" != "$STAGING_IMAGE_DIGEST" ]]; then
	echo "The staging candidate tag does not resolve to the approved image digest."
	exit 1
fi
emit_github_output image_digest "$STAGING_IMAGE_DIGEST" || exit 1
if ! RECOVERY_MANIFEST_DIGEST=$(az acr manifest show-metadata \
	--registry "$ACR_NAME" --name "$STAGING_REPOSITORY@$EXPECTED_RECOVERY_DIGEST" \
	--query digest -o tsv); then
	echo "The reviewed recovery image is not available in the staging registry."
	exit 1
fi
if [[ "$RECOVERY_MANIFEST_DIGEST" != "$EXPECTED_RECOVERY_DIGEST" ]]; then
	echo "The staging recovery manifest does not match the reviewed digest."
	exit 1
fi
if ! ACR_USERNAME=$(az acr credential show \
	-n "$ACR_NAME" --query "username" -o tsv); then
	echo "Failed to resolve the staging registry username."
	exit 1
fi
if ! ACR_PASSWORD=$(az acr credential show \
	-n "$ACR_NAME" --query "passwords[0].value" -o tsv); then
	echo "Failed to resolve the staging registry credential."
	exit 1
fi
ACR_SERVER=$(echo "$STAGING_IMAGE" | cut -d'/' -f1)

resolve_staging_digest() {
	local image_reference=$1
	local digest
	case "$image_reference" in
	"$STAGING_IMAGE"@sha256:*)
		digest=${image_reference#*@}
		;;
	*)
		return 1
		;;
	esac
	[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
	printf '%s\n' "$digest"
}

collect_workload_state() {
	local mode=${1:-safe}
	local container workload_json ready_revision revision_json state_json states_json
	local secret_names_json public_names_json expected_args_json
	local container_secret_vars=() container_public_vars=()
	[[ "$mode" == "safe" || "$mode" == "exact" ]] || return 1
	local states=()
	for container in $CRITICAL_CONTAINERS; do
		configure_workload_contract "$container"
		secret_names_json=$(printf '%s\n' "${container_secret_vars[@]}" |
			jq -Rsc 'split("\n") | map(select(length > 0))') || return 1
		public_names_json=$(printf '%s\n' "${container_public_vars[@]}" |
			jq -Rsc 'split("\n") | map(select(length > 0))') || return 1
		case "$container" in
		"$API_CONTAINER") expected_args_json='[]' ;;
		"$WORKER_CONTAINER") expected_args_json=$(jq -cn --arg value "$STAGING_WORKER_COMMAND" '[$value]') ;;
		"$SCHEDULER_CONTAINER") expected_args_json=$(jq -cn --arg value "$SCHEDULER_COMMAND" '[$value]') ;;
		esac
		workload_json=$(az containerapp show \
			-n "$container" -g "$RESOURCE_GROUP" \
			--query '{name:name,latestRevision:properties.latestRevisionName,latestReadyRevision:properties.latestReadyRevisionName,activeRevisionsMode:properties.configuration.activeRevisionsMode,traffic:properties.configuration.ingress.traffic}' \
			-o json) || {
			echo "Failed to inspect staging workload $container." >&2
			return 1
		}
		ready_revision=$(jq -er --arg expected "$container" '
			select(type == "object" and .name == $expected and
			 (.latestRevision | type == "string" and length > 0) and
			 (.latestReadyRevision | type == "string" and length > 0) and
			 (.activeRevisionsMode | type == "string" and length > 0) and
			 (.traffic == null or (.traffic | type == "array"))) |
			.latestReadyRevision
		' <<<"$workload_json") || {
			echo "Staging workload metadata for $container is incomplete or mismatched." >&2
			return 1
		}
		revision_json=$(az containerapp revision show \
			-n "$container" -g "$RESOURCE_GROUP" \
			--revision "$ready_revision" \
			--query '{containers:properties.template.containers[].{name:name,image:image,command:command,args:args,env:env[].{name:name,value:value,secretRef:secretRef}},health:properties.healthState,running:properties.runningState,provisioning:properties.provisioningState}' \
			-o json) || {
			echo "Failed to inspect ready revision $ready_revision for $container." >&2
			return 1
		}
		state_json=$(printf '%s\n%s\n%s\n' \
			"$workload_json" "$revision_json" "$ENV_JSON" | jq -ces \
			--argjson secret_names "$secret_names_json" \
			--argjson public_names "$public_names_json" \
			--argjson expected_args "$expected_args_json" \
			--arg expected_name "$container" \
			--arg expected_image "$STAGING_IMAGE_REFERENCE" \
			--arg mode "$mode" '
			def secret_ref($name): $name | ascii_downcase | gsub("_"; "-");
			.[0] as $workload | .[1] as $revision | .[2] as $configuration |
			select(($revision.containers | type == "array") and
			 ($revision.containers | length) == 1) |
			$revision.containers[0] as $container |
			($container.env // []) as $environment |
			select($container.name == $expected_name and
			 ($container.image | type == "string" and length > 0) and
			 ($container.command == null or ($container.command | type == "array")) and
			 ($container.args == null or ($container.args | type == "array")) and
			 ($environment | type == "array") and
			 (($environment | map(.name) | unique | length) == ($environment | length)) and
			 all($environment[];
			   .name as $name |
			   ($name | type == "string" and length > 0) and
			   if ($secret_names | index($name)) != null then
			     .secretRef == secret_ref($name) and (.value == null or .value == "")
			   elif ($public_names | index($name)) != null then
			     (.value | type == "string") and .secretRef == null
			   else false end)) |
			($environment | map(select(.secretRef != null) |
			 {key: .name, value: .secretRef}) | from_entries) as $secret_refs |
			($environment | map(select(.secretRef == null) |
			 {key: .name, value: .value}) | from_entries) as $public_config |
			(reduce $secret_names[] as $name ({};
			  if (($configuration[$name] // "") | type == "string" and length > 0)
			  then . + {($name): secret_ref($name)} else . end)) as $expected_secrets |
			(reduce $public_names[] as $name ({};
			  if (($configuration[$name] // "") | type == "string" and length > 0)
			  then . + {($name): $configuration[$name]} else . end)) as $expected_public |
			select($mode != "exact" or
			 ($container.image == $expected_image and ($container.command // []) == [] and
			  ($container.args // []) == $expected_args and
			  $secret_refs == $expected_secrets and $public_config == $expected_public)) |
			{
			 name: $workload.name, latestRevision: $workload.latestRevision,
			 latestReadyRevision: $workload.latestReadyRevision,
			 containerCount: ($revision.containers | length),
			 readyRevisionImage: $container.image,
			 readyRevisionCommand: ($container.command // []),
			 readyRevisionArgs: ($container.args // []),
			 readyRevisionHealth: $revision.health, readyRevisionRunning: $revision.running,
			 readyRevisionProvisioning: $revision.provisioning,
			 activeRevisionsMode: $workload.activeRevisionsMode, traffic: $workload.traffic,
			 publicConfig: $public_config, secretRefs: $secret_refs,
			 environmentContractExact: ($mode == "exact")
			}
		') || {
			echo "Ready revision metadata for $container is invalid." >&2
			return 1
		}
		states+=("$state_json")
	done
	states_json=$(printf '%s\n' "${states[@]}" | jq -ecsS \
		--arg api "$API_CONTAINER" \
		--arg worker "$WORKER_CONTAINER" \
		--arg scheduler "$SCHEDULER_CONTAINER" '
		sort_by(.name) |
		select(length == 3 and (([.[].name] | sort) == ([$api, $worker, $scheduler] | sort)))
	') || {
		echo "Staging workload evidence did not contain exactly the three expected workloads." >&2
		return 1
	}
	printf '%s\n' "$states_json"
}

verify_workload_state_ready() {
	local state_json=$1
	if ! jq -e --arg api "$API_CONTAINER" '
		all(.[]; .latestRevision == .latestReadyRevision and
		 .activeRevisionsMode == "Single" and .readyRevisionHealth == "Healthy" and
		 .readyRevisionProvisioning == "Provisioned" and
		 (.readyRevisionRunning == "Running" or .readyRevisionRunning == "RunningAtMaxScale")) and
		([.[] | select(.name == $api)] | length == 1) and
		([.[] | select(.name == $api)][0]) as $app |
		([$app.traffic[]? | select((.weight // 0) > 0)]) as $routes |
		($routes | length) == 1 and $routes[0].weight == 100 and
		($routes[0].latestRevision == true or $routes[0].revisionName == $app.latestReadyRevision)
	' <<<"$state_json" >/dev/null; then
		echo "Staging workloads are not uniformly ready in Single mode with API traffic on the ready revision."
		return 1
	fi
}

verify_candidate_workload_state() {
	local state_json=$1
	if ! jq -e \
		--arg expected_image "$STAGING_IMAGE_REFERENCE" \
		--arg worker "$WORKER_CONTAINER" \
		--arg scheduler "$SCHEDULER_CONTAINER" \
		--arg worker_command "$STAGING_WORKER_COMMAND" \
		--arg scheduler_command "$SCHEDULER_COMMAND" \
		--argjson expected "$CONFIGURATION_EVIDENCE_JSON" '
		($expected.urls + $expected.flags + {PYTHON_ENV: $expected.environment}) as $all |
		($all | del(.STRIPE_INTEGRATION_ENABLED, .STRIPE_BILLING_ENABLED, .STRIPE_LIVE_MODE)) as $common |
		all(.[]; . as $workload | $workload.environmentContractExact == true and
		 $workload.readyRevisionCommand == [] and
		 $workload.readyRevisionImage == $expected_image and
		 ($common | to_entries | all(.[] as $item; $workload.publicConfig[$item.key] == $item.value))) and
		all(.[] | select(.name != $scheduler); . as $workload | ($expected.flags |
		 {STRIPE_INTEGRATION_ENABLED, STRIPE_BILLING_ENABLED, STRIPE_LIVE_MODE} |
		 to_entries | all(.[] as $item; $workload.publicConfig[$item.key] == $item.value))) and
		([.[] | select(.name == $worker)][0].readyRevisionArgs == [$worker_command]) and
		([.[] | select(.name == $scheduler)][0].readyRevisionArgs == [$scheduler_command])
	' <<<"$state_json" >/dev/null; then
		echo "Candidate workload image or disabled feature-gate evidence is inconsistent."
		return 1
	fi
}

capture_failed_workload_state() {
	local exit_status=$? failure_state
	trap - EXIT
	if [[ "$exit_status" -ne 0 && "${WORKLOAD_CAPTURE_READY:-false}" == "true" ]]; then
		failure_state=$(collect_workload_state) &&
			emit_github_output workloads_after "$failure_state" ||
			echo "Failed to capture workload state after the deployment error."
	fi
	exit "$exit_status"
}

WORKLOAD_CAPTURE_READY=true
trap capture_failed_workload_state EXIT

if ! WORKLOADS_BEFORE_JSON=$(collect_workload_state safe); then
	echo "Failed to capture pre-deployment workload revisions."
	exit 1
fi
emit_github_output workloads_before "$WORKLOADS_BEFORE_JSON" || exit 1
if ! verify_workload_state_ready "$WORKLOADS_BEFORE_JSON"; then
	echo "Pre-deployment staging workload state is not safe to mutate."
	exit 1
fi
RECOVERY_DIGEST=""
while IFS= read -r current_image; do
	if ! current_digest=$(resolve_staging_digest "$current_image"); then
		echo "A current staging workload is not pinned to an immutable staging digest."
		exit 1
	fi
	if [[ -z "$RECOVERY_DIGEST" ]]; then
		RECOVERY_DIGEST=$current_digest
	elif [[ "$RECOVERY_DIGEST" != "$current_digest" ]]; then
		echo "Staging workloads do not share one immutable recovery digest."
		exit 1
	fi
done < <(jq -r '.[].readyRevisionImage' <<<"$WORKLOADS_BEFORE_JSON")
if [[ "$RECOVERY_DIGEST" != "$EXPECTED_RECOVERY_DIGEST" ]]; then
	echo "Live staging does not match the reviewed recovery digest."
	exit 1
fi
emit_github_output recovery_digest "$RECOVERY_DIGEST" || exit 1

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
			--expected-image "$STAGING_IMAGE_REFERENCE" \
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

capture_schema_revision() {
	local phase=$1
	local migration_container_name=$2
	local execution_name logs revision attempts
	local schema_probe payload
	read -r -d '' schema_probe <<'PY' || true
import os,re
import psycopg
with psycopg.connect(os.environ["DATABASE_URL"],connect_timeout=10,options="-c default_transaction_read_only=on -c statement_timeout=15000") as connection:
    rows=connection.execute("SELECT version_num FROM public.alembic_version").fetchall()
if len(rows)!=1 or not isinstance(rows[0][0],str) or re.fullmatch(r"[0-9a-f]{12}",rows[0][0]) is None:
    raise SystemExit(1)
print("STAGING_SCHEMA_REVISION="+rows[0][0])
PY
	payload=$(printf '%s' "$schema_probe" | python3 -c \
		'import base64,sys,zlib;print(base64.b64encode(zlib.compress(sys.stdin.buffer.read(),9)).decode())')
	if ! execution_name=$(az containerapp job start \
		-n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
		--container-name "$migration_container_name" \
		--image "$STAGING_IMAGE_REFERENCE" \
		--command /opt/venv/bin/python \
		--args -c "import base64,zlib;exec(zlib.decompress(base64.b64decode('$payload')))" \
		--env-vars "DATABASE_URL=secretref:database-url" \
		--query "name" -o tsv); then
		echo "The $phase schema evidence probe failed to start."
		return 1
	fi
	wait_for_migration_job_execution "$execution_name" "$phase schema evidence" || return 1

	attempts=0
	while [[ $attempts -lt 12 ]]; do
		logs=$(az containerapp job logs show \
			-n "$MIGRATION_JOB" -g "$RESOURCE_GROUP" \
			--execution "$execution_name" --container "$migration_container_name" \
			--format text --tail 30 2>/dev/null || true)
		revision=$(printf '%s\n' "$logs" |
			grep -Eo 'STAGING_SCHEMA_REVISION=[0-9a-f]{12}' |
			cut -d= -f2 | sort -u || true)
		if [[ "$revision" =~ ^[0-9a-f]{12}$ ]]; then
			CAPTURED_SCHEMA_REVISION=$revision
			echo "$phase schema revision captured."
			return 0
		fi
		sleep 5
		((attempts++))
	done
	echo "The $phase schema revision could not be recorded unambiguously."
	return 1
}

run_migrations() {
	echo "Running database migrations with immutable image $STAGING_IMAGE_REFERENCE..."

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
		--image "$STAGING_IMAGE_REFERENCE" \
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
	capture_schema_revision "Pre-migration" "$migration_container_name" || return 1
	SCHEMA_BEFORE=$CAPTURED_SCHEMA_REVISION
	emit_github_output schema_before "$SCHEMA_BEFORE" || return 1
	if [[ "$SCHEMA_BEFORE" != "$EXPECTED_SCHEMA_BEFORE" ]]; then
		echo "Live staging schema does not match the reviewed pre-migration revision."
		return 1
	fi
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
		--image "$STAGING_IMAGE_REFERENCE" \
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
	local migration_status=0
	wait_for_migration_job_execution "$execution_name" "Migrations" || \
		migration_status=$?
	capture_schema_revision "Post-migration" "$migration_container_name" || return 1
	SCHEMA_AFTER=$CAPTURED_SCHEMA_REVISION
	emit_github_output schema_after "$SCHEMA_AFTER" || return 1
	if [[ "$migration_status" -ne 0 ]]; then
		echo "Migration execution failed; the observed post-attempt schema was recorded."
		return "$migration_status"
	fi
	if [[ "$SCHEMA_AFTER" != "$EXPECTED_SCHEMA_AFTER" ]]; then
		echo "Live staging schema does not match the reviewed post-migration revision."
		return 1
	fi
}

initiate_deployment() {
	local container_name=$1
	local git_sha=$2
	local image="$STAGING_IMAGE_REFERENCE"

	# Build secrets and env var args — only include vars that have a value in 1Password.
	# We skip missing vars so we never reference a secretref that doesn't exist.
	local secrets_args=()
	local env_args=()
	local remove_env_vars=()
	local container_secret_vars=() container_public_vars=()
	configure_workload_contract "$container_name"

	for var in "${container_secret_vars[@]}"; do
		local value
		value=$(echo "$ENV_JSON" | jq -r ".\"$var\" // empty")
		if [[ -z "$value" ]]; then
			remove_env_vars+=("$var")
			continue
		fi
		local secret_name
		secret_name=$(echo "$var" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
		secrets_args+=("${secret_name}=${value}")
		env_args+=("${var}=secretref:${secret_name}")
	done

	for var in "${container_public_vars[@]}"; do
		local value
		value=$(echo "$ENV_JSON" | jq -r ".\"$var\" // empty")
		if [[ -n "$value" ]]; then
			env_args+=("${var}=${value}")
		else
			remove_env_vars+=("$var")
		fi
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
	local expected_image="$STAGING_IMAGE_REFERENCE"
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

wait_for_incompatible_worker_replicas_to_drain() {
	local container_name=$1
	local git_sha=$2
	local expected_image="$STAGING_IMAGE_REFERENCE"
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
verify_founder_release_source || exit 1
verify_azure_deployment_identity || exit 1
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
	if [[ "$container" == "$WORKER_CONTAINER" ]]; then
		wait_for_incompatible_worker_replicas_to_drain "$container" "$GIT_SHA" || exit 1
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

if ! WORKLOADS_AFTER_JSON=$(collect_workload_state exact); then
	echo "Failed to capture post-deployment workload revisions."
	exit 1
fi
emit_github_output workloads_after "$WORKLOADS_AFTER_JSON" || exit 1
if ! verify_workload_state_ready "$WORKLOADS_AFTER_JSON"; then
	echo "Post-deployment staging workload state is not ready."
	exit 1
fi
if ! verify_candidate_workload_state "$WORKLOADS_AFTER_JSON"; then
	echo "Post-deployment staging workloads do not match the reviewed candidate."
	exit 1
fi

echo "All critical deployments are successful for Git SHA $GIT_SHA."
exit 0
