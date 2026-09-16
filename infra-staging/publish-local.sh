#!/usr/bin/env bash

# Publish one reviewed, credential-free local build to the existing staging ACR.
# This script never deploys, migrates, writes `latest`, retries, or targets production.
# shellcheck disable=SC2016 # jq programs intentionally use literal $variables.

set -euo pipefail
umask 077

if [[ $# -ne 3 ]]; then
	echo "Usage: $0 BUILD_PLAN EXPECTED_PLAN_SHA256 CONTROLLER_RESULT" >&2
	exit 2
fi

BUILD_PLAN=$1
EXPECTED_PLAN_SHA256=$2
CONTROLLER_RESULT=$3
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON=/opt/homebrew/bin/python3
GH=/opt/homebrew/Cellar/gh/2.97.0/bin/gh
AZ=/opt/homebrew/bin/az
DOCKER=/opt/homebrew/Cellar/docker/29.7.2/bin/docker
JQ=/usr/bin/jq
SED=/usr/bin/sed
TAIL=/usr/bin/tail
MKTEMP=/usr/bin/mktemp
RM=/bin/rm
RMDIR=/bin/rmdir
STAT=/usr/bin/stat
ID=/usr/bin/id
CHMOD=/bin/chmod
export PATH=/opt/homebrew/Cellar/gh/2.97.0/bin:/opt/homebrew/bin:/usr/bin:/bin

CANDIDATE_SHA=866bd4bc01e0e7be32c59eccdaf9dac3f495c7de
APPROVAL_PR_NUMBER=385
EXPECTED_SCHEMA_AFTER=9a2399212488
REGISTRY_NAME=cosmaneurastagingregistry
REGISTRY_HOST=cosmaneurastagingregistry.azurecr.io
REGISTRY_REPOSITORY=cosmaneura/cosmaneura
CANDIDATE_IMAGE="$REGISTRY_HOST/$REGISTRY_REPOSITORY:$CANDIDATE_SHA"
PINNED_SUBSCRIPTION_ID=bab2a709-1fcb-4c87-a9fc-802f7477ec13
PINNED_TENANT_ID=d51754d0-bba5-4810-820e-afac542844d6
PINNED_FOUNDER_GITHUB_IDENTITY_SHA256=a4deb86ff0cd6ba7c4e14747a1f39526160ad161ae2ad2a4bb170d8d5d5d80f5
PINNED_FOUNDER_AZURE_USERNAME_SHA256=acf2c107f43fa6950918e5f089e1b865ba56188295b88d3d72ad342d742e616a
PINNED_FOUNDER_AZURE_OBJECT_ID_SHA256=0839f64960f7e378762f2f1aab2d324b197c4c0d2198f87d9f595df4eb0b1f93

AUTH_DIR=""
EVIDENCE_PATH=""
PHASE=validate_inputs
PUSH_STARTED=false
IMAGE_DIGEST=""

hash_stdin() {
	"$PYTHON" -I -S -B -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}

secure_file_fingerprint() {
	"$PYTHON" -I -S -B - "$1" "$2" <<'PY'
import hashlib
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
maximum = int(sys.argv[2])
if not path.is_absolute() or path.resolve() != path:
    raise SystemExit(1)
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
try:
    before = os.fstat(fd)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) != 0o400
        or not 0 < before.st_size <= maximum
    ):
        raise SystemExit(1)
    digest = hashlib.sha256()
    size = 0
    while chunk := os.read(fd, min(1024 * 1024, maximum - size + 1)):
        size += len(chunk)
        if size > maximum:
            raise SystemExit(1)
        digest.update(chunk)
    after = os.fstat(fd)
    fields = (
        "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
        "st_size", "st_mtime_ns", "st_ctime_ns",
    )
    if size != before.st_size or any(
        getattr(before, field) != getattr(after, field) for field in fields
    ):
        raise SystemExit(1)
    print(f"{digest.hexdigest()} {size}")
finally:
    os.close(fd)
PY
}

canonical_github_user_sha256() {
	"$PYTHON" -I -S -B -c '
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

verify_founder_source() {
	local origin dirty controller_sha identity_json identity_sha256 actions_json
	origin=$(/usr/bin/git -C "$REPOSITORY_ROOT" remote get-url origin)
	dirty=$(/usr/bin/git -C "$REPOSITORY_ROOT" status --porcelain=v1 --untracked-files=all)
	if [[ "$origin" != "https://github.com/CosmaNeura/cosmaneura.git" || -n "$dirty" ]]; then
		echo "Local publication requires the clean canonical repository." >&2
		return 1
	fi
	controller_sha=$(/usr/bin/git -C "$REPOSITORY_ROOT" rev-parse HEAD)
	if [[ "$controller_sha" != "$PLAN_CONTROLLER_SHA" ]]; then
		echo "Local publication controller does not match the reviewed build plan." >&2
		return 1
	fi
	identity_json=$("$GH" api --hostname github.com --method GET user)
	identity_sha256=$(printf '%s' "$identity_json" | canonical_github_user_sha256)
	unset identity_json
	if [[ "$identity_sha256" != "$PINNED_FOUNDER_GITHUB_IDENTITY_SHA256" ]]; then
		echo "The local GitHub operator is not the pinned founder." >&2
		return 1
	fi
	actions_json=$("$GH" api --hostname github.com --method GET \
		repos/CosmaNeura/cosmaneura/actions/permissions)
	if ! printf '%s' "$actions_json" | "$JQ" -e '.enabled == false' >/dev/null; then
		echo "GitHub Actions must remain disabled during local publication." >&2
		return 1
	fi
	"$PYTHON" -I -S -B "$REPOSITORY_ROOT/scripts/verify_staging_candidate.py" \
		--candidate-sha "$CANDIDATE_SHA" \
		--approval-pr-number "$APPROVAL_PR_NUMBER" \
		--expected-schema-after "$EXPECTED_SCHEMA_AFTER" \
		--workflow-sha "$PLAN_CONTROLLER_SHA" \
		--repository CosmaNeura/cosmaneura \
		--event-name workflow_dispatch \
		--ref refs/heads/master \
		--ref-protected true \
		--actor JohnClem \
		--triggering-actor JohnClem >/dev/null
}

verify_founder_azure_identity() {
	local account_json username username_sha256 object_id object_id_sha256
	if [[ -n "${AZURE_CLIENT_ID:-}" || -n "${AZURE_TENANT_ID:-}" || \
		-n "${AZURE_SUBSCRIPTION_ID:-}" || -n "${AZURE_CLIENT_SECRET:-}" || \
		-n "${AZURE_CLIENT_CERTIFICATE_PATH:-}" ]]; then
		echo "Local publication refuses configured service-principal inputs." >&2
		return 1
	fi
	account_json=$("$AZ" account show --subscription "$PINNED_SUBSCRIPTION_ID" \
		--query '{subscriptionId:id,tenantId:tenantId,userName:user.name,userType:user.type}' \
		--output json)
	username=$(printf '%s' "$account_json" | "$JQ" -er \
		--arg subscription "$PINNED_SUBSCRIPTION_ID" \
		--arg tenant "$PINNED_TENANT_ID" \
		'if .subscriptionId == $subscription and .tenantId == $tenant and
		 .userType == "user" and (.userName | type) == "string" and
		 (.userName | length) > 0 then .userName else error("identity mismatch") end')
	unset account_json
	username_sha256=$(printf '%s' "$username" | hash_stdin)
	unset username
	if [[ "$username_sha256" != "$PINNED_FOUNDER_AZURE_USERNAME_SHA256" ]]; then
		echo "The active Azure user is not the pinned founder." >&2
		return 1
	fi
	object_id=$("$AZ" ad signed-in-user show --query id --output tsv)
	if [[ ! "$object_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
		echo "The Azure directory user identity is invalid." >&2
		return 1
	fi
	object_id_sha256=$(printf '%s' "$object_id" | hash_stdin)
	unset object_id
	if [[ "$object_id_sha256" != "$PINNED_FOUNDER_AZURE_OBJECT_ID_SHA256" ]]; then
		echo "The Azure directory user is not the pinned founder." >&2
		return 1
	fi
}

remote_tag_absent() {
	local tags
	tags=$("$AZ" acr repository show-tags \
		--name "$REGISTRY_NAME" \
		--repository "$REGISTRY_REPOSITORY" \
		--subscription "$PINNED_SUBSCRIPTION_ID" \
		--query "[?@=='$CANDIDATE_SHA']" \
		--output json)
	printf '%s' "$tags" | "$JQ" -e 'type == "array" and length == 0' >/dev/null
}

revalidate_build_plan() {
	local observed_sha256 observed_bytes check_result
	read -r observed_sha256 observed_bytes < <(secure_file_fingerprint "$BUILD_PLAN" 65536)
	[[ "$observed_sha256" == "$EXPECTED_PLAN_SHA256" && "$observed_bytes" -gt 0 ]] || {
		echo "Build plan does not match the reviewed digest." >&2
		return 1
	}
	check_result=$("$PYTHON" -I -S -B "$REPOSITORY_ROOT/scripts/staging_build_controller.py" \
		--check-only "$BUILD_PLAN" "$EXPECTED_PLAN_SHA256")
	printf '%s' "$check_result" | "$JQ" -e \
		'.local_bindings_passed == true and .authenticated_source_checked == false and .build_started == false' >/dev/null
}

write_evidence() {
	local complete=$1 outcome=$2
	[[ -n "$EVIDENCE_PATH" && ! -e "$EVIDENCE_PATH" ]] || return 0
	local payload
	payload=$("$JQ" -cn \
		--arg recorded_at "$(/bin/date -u +%FT%TZ)" \
		--arg phase "$PHASE" \
		--arg outcome "$outcome" \
		--arg candidate_sha "$CANDIDATE_SHA" \
		--arg controller_sha "$PLAN_CONTROLLER_SHA" \
		--arg build_plan_sha256 "$EXPECTED_PLAN_SHA256" \
		--arg image_id "$IMAGE_ID" \
		--arg archive_sha256 "$ARCHIVE_SHA256" \
		--arg image_digest "$IMAGE_DIGEST" \
		--arg image_reference "$REGISTRY_HOST/$REGISTRY_REPOSITORY${IMAGE_DIGEST:+@$IMAGE_DIGEST}" \
		--argjson complete "$complete" \
		--argjson push_started "$PUSH_STARTED" \
		'{schema_version:1,recorded_at:$recorded_at,complete:$complete,outcome:$outcome,
		  phase:$phase,candidate_sha:$candidate_sha,controller_sha:$controller_sha,
		  build_plan_sha256:$build_plan_sha256,image_id:$image_id,
		  archive_sha256:$archive_sha256,push_started:$push_started,
		  image_digest:(if $image_digest == "" then null else $image_digest end),
		  image_reference:(if $image_digest == "" then null else $image_reference end),
		  automatic_retry_allowed:false,deployment_performed:false,production_operation:false}')
	printf '%s\n' "$payload" | "$PYTHON" -I -S -B -c '
import os
import sys

path = sys.argv[1]
raw = sys.stdin.buffer.read()
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
try:
    with os.fdopen(fd, "wb", closefd=False) as stream:
        if stream.write(raw) != len(raw):
            raise OSError("short evidence write")
        stream.flush()
        os.fsync(stream.fileno())
finally:
    os.close(fd)
' "$EVIDENCE_PATH"
}

cleanup() {
	local status=$?
	set +e
	unset ACR_TOKEN ACR_LOGIN_JSON
	if [[ -n "$AUTH_DIR" && "$AUTH_DIR" =~ ^/private/tmp/cosmaneura-staging-acr-auth\.[A-Za-z0-9]+$ && \
		-d "$AUTH_DIR" && ! -L "$AUTH_DIR" && "$("$STAT" -f %u "$AUTH_DIR")" == "$("$ID" -u)" ]]; then
		"$DOCKER" logout "$REGISTRY_HOST" >/dev/null 2>&1
		if [[ -f "$AUTH_DIR/config.json" && ! -L "$AUTH_DIR/config.json" ]]; then
			"$RM" -f -- "$AUTH_DIR/config.json"
		fi
		"$RMDIR" "$AUTH_DIR" >/dev/null 2>&1
	fi
	if [[ $status -ne 0 ]]; then
		write_evidence false failed
	fi
	exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

for tool in "$PYTHON" "$GH" "$AZ" "$DOCKER" "$JQ" "$SED" "$TAIL" "$MKTEMP" "$RM" "$RMDIR" "$STAT" "$ID" "$CHMOD"; do
	[[ -x "$tool" ]] || { echo "Required publication tool is unavailable." >&2; exit 1; }
done
if [[ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" || -n "${RECALL_WEBHOOK_SECRET:-}" || \
	-n "${DOCKER_AUTH_CONFIG:-}" ]]; then
	echo "Local publication refuses deployment or ambient registry credentials." >&2
	exit 1
fi
unset GH_TOKEN GITHUB_TOKEN DOCKER_TLS_VERIFY DOCKER_CERT_PATH
[[ "$EXPECTED_PLAN_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "Invalid build-plan digest." >&2; exit 1; }

read -r observed_plan_sha256 observed_plan_bytes < <(secure_file_fingerprint "$BUILD_PLAN" 65536)
[[ "$observed_plan_sha256" == "$EXPECTED_PLAN_SHA256" && "$observed_plan_bytes" -gt 0 ]] || exit 1
PLAN_CONTROLLER_SHA=$("$JQ" -er '.controller_sha | select(test("^[0-9a-f]{40}$"))' "$BUILD_PLAN")
[[ "$("$JQ" -er '.candidate_sha' "$BUILD_PLAN")" == "$CANDIDATE_SHA" ]] || exit 1
[[ "$("$JQ" -er '.candidate_pr' "$BUILD_PLAN")" == "$APPROVAL_PR_NUMBER" ]] || exit 1

read -r _result_sha256 _result_bytes < <(secure_file_fingerprint "$CONTROLLER_RESULT" 65536)
BUILD_ROOT=$("$JQ" -er '.build_root' "$CONTROLLER_RESULT")
[[ "$BUILD_ROOT" =~ ^/private/tmp/cosmaneura-staging-build-[0-9a-f]{32}$ ]] || exit 1
[[ "$CONTROLLER_RESULT" == "$BUILD_ROOT/controller-result.json" ]] || exit 1
[[ "$("$JQ" -er '.complete' "$CONTROLLER_RESULT")" == true ]] || exit 1
[[ "$("$JQ" -er '.candidate_sha' "$CONTROLLER_RESULT")" == "$CANDIDATE_SHA" ]] || exit 1
[[ "$("$JQ" -er '.controller_sha' "$CONTROLLER_RESULT")" == "$PLAN_CONTROLLER_SHA" ]] || exit 1
[[ "$("$JQ" -er '.plan_sha256' "$CONTROLLER_RESULT")" == "$EXPECTED_PLAN_SHA256" ]] || exit 1
[[ "$("$JQ" -er '.publication_authorized' "$CONTROLLER_RESULT")" == false ]] || exit 1
[[ "$("$JQ" -er '.deployment_authorized' "$CONTROLLER_RESULT")" == false ]] || exit 1

IMAGE_ID=$("$JQ" -er '.image_id | select(test("^sha256:[0-9a-f]{64}$"))' "$CONTROLLER_RESULT")
IMAGE_ARCHIVE=$("$JQ" -er '.image_archive' "$CONTROLLER_RESULT")
ARCHIVE_SHA256=$("$JQ" -er '.image_archive_sha256 | select(test("^[0-9a-f]{64}$"))' "$CONTROLLER_RESULT")
ARCHIVE_BYTES=$("$JQ" -er '.image_archive_bytes | select(type == "number" and . > 0 and . <= 17179869184)' "$CONTROLLER_RESULT")
BUILD_METADATA=$("$JQ" -er '.build_metadata' "$CONTROLLER_RESULT")
[[ "$IMAGE_ARCHIVE" == "$BUILD_ROOT/artifacts/application-image.tar" ]] || exit 1
[[ "$BUILD_METADATA" == "$BUILD_ROOT/artifacts/build-result.json" ]] || exit 1
EVIDENCE_PATH="$BUILD_ROOT/artifacts/publication-result.json"
[[ ! -e "$EVIDENCE_PATH" ]] || { echo "Publication evidence already exists; refusing a retry." >&2; exit 1; }

read -r observed_archive_sha256 observed_archive_bytes < <(secure_file_fingerprint "$IMAGE_ARCHIVE" 17179869184)
[[ "$observed_archive_sha256" == "$ARCHIVE_SHA256" && "$observed_archive_bytes" == "$ARCHIVE_BYTES" ]] || exit 1
read -r _metadata_sha256 _metadata_bytes < <(secure_file_fingerprint "$BUILD_METADATA" 65536)
[[ "$("$JQ" -er '.candidate_sha' "$BUILD_METADATA")" == "$CANDIDATE_SHA" ]] || exit 1
[[ "$("$JQ" -er '.image_id' "$BUILD_METADATA")" == "$IMAGE_ID" ]] || exit 1
[[ "$("$JQ" -er '.archive_sha256' "$BUILD_METADATA")" == "$ARCHIVE_SHA256" ]] || exit 1
[[ "$("$JQ" -er '.archive_bytes' "$BUILD_METADATA")" == "$ARCHIVE_BYTES" ]] || exit 1
[[ "$("$JQ" -er '.publication_authorized' "$BUILD_METADATA")" == false ]] || exit 1
[[ "$("$JQ" -er '.deployment_authorized' "$BUILD_METADATA")" == false ]] || exit 1

PHASE=revalidate_build_plan
revalidate_build_plan

PHASE=verify_source_and_identity
verify_founder_source
verify_founder_azure_identity

PHASE=load_and_verify_archive
AUTH_DIR=$("$MKTEMP" -d /private/tmp/cosmaneura-staging-acr-auth.XXXXXXXX)
[[ "$AUTH_DIR" =~ ^/private/tmp/cosmaneura-staging-acr-auth\.[A-Za-z0-9]+$ && \
	-d "$AUTH_DIR" && ! -L "$AUTH_DIR" ]] || exit 1
"$CHMOD" 700 "$AUTH_DIR"
export DOCKER_CONFIG="$AUTH_DIR"
export DOCKER_HOST=unix:///Users/jw/.colima/default/docker.sock
"$DOCKER" image load --input "$IMAGE_ARCHIVE" >/dev/null
image_json=$("$DOCKER" image inspect "$IMAGE_ID")
printf '%s' "$image_json" | "$JQ" -e \
	--arg image_id "$IMAGE_ID" --arg candidate "$CANDIDATE_SHA" '
	type == "array" and length == 1 and .[0].Id == $image_id and
	.[0].Os == "linux" and .[0].Architecture == "amd64" and
	.[0].Config.Labels["org.opencontainers.image.revision"] == $candidate and
	.[0].Config.Labels["org.opencontainers.image.source"] == "https://github.com/CosmaNeura/cosmaneura"' >/dev/null
unset image_json
[[ -z "$("$DOCKER" image ls --quiet --no-trunc --filter "reference=$CANDIDATE_IMAGE")" ]] || {
	echo "The local immutable candidate tag already exists; refusing to replace it." >&2
	exit 1
}
remote_tag_absent || { echo "The remote immutable candidate tag is not absent." >&2; exit 1; }

PHASE=registry_login
ACR_LOGIN_JSON=$("$AZ" acr login --name "$REGISTRY_NAME" \
	--subscription "$PINNED_SUBSCRIPTION_ID" --expose-token --output json)
[[ "$(printf '%s' "$ACR_LOGIN_JSON" | "$JQ" -er '.loginServer')" == "$REGISTRY_HOST" ]] || exit 1
ACR_TOKEN=$(printf '%s' "$ACR_LOGIN_JSON" | "$JQ" -er '.accessToken | select(type == "string" and length > 0)')
unset ACR_LOGIN_JSON
printf '%s' "$ACR_TOKEN" | "$DOCKER" login "$REGISTRY_HOST" \
	--username 00000000-0000-0000-0000-000000000000 --password-stdin >/dev/null
unset ACR_TOKEN

PHASE=final_prepublication_verification
revalidate_build_plan
verify_founder_source
verify_founder_azure_identity
read -r final_archive_sha256 final_archive_bytes < <(secure_file_fingerprint "$IMAGE_ARCHIVE" 17179869184)
[[ "$final_archive_sha256" == "$ARCHIVE_SHA256" && "$final_archive_bytes" == "$ARCHIVE_BYTES" ]] || exit 1
[[ "$("$DOCKER" image inspect --format '{{.Id}}' "$IMAGE_ID")" == "$IMAGE_ID" ]] || exit 1
remote_tag_absent || { echo "The remote immutable candidate tag is not absent." >&2; exit 1; }

PHASE=push_started
PUSH_STARTED=true
"$DOCKER" tag "$IMAGE_ID" "$CANDIDATE_IMAGE"
[[ "$("$DOCKER" image inspect --format '{{.Id}}' "$CANDIDATE_IMAGE")" == "$IMAGE_ID" ]] || exit 1
push_output=$("$DOCKER" push "$CANDIDATE_IMAGE" 2>&1)
printf '%s\n' "$push_output"
IMAGE_DIGEST=$(printf '%s\n' "$push_output" | "$SED" -nE \
	's/^digest: (sha256:[0-9a-f]{64}) size: [0-9]+$/\1/p' | "$TAIL" -n 1)
unset push_output
[[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || {
	echo "The publication did not return an immutable digest." >&2
	exit 1
}

PHASE=verify_remote_digest
REMOTE_DIGEST=$("$AZ" acr manifest show-metadata \
	--registry "$REGISTRY_NAME" \
	--name "$REGISTRY_REPOSITORY:$CANDIDATE_SHA" \
	--subscription "$PINNED_SUBSCRIPTION_ID" \
	--query digest --output tsv)
[[ "$REMOTE_DIGEST" == "$IMAGE_DIGEST" ]] || {
	echo "The registry digest does not match the pushed candidate." >&2
	exit 1
}

PHASE=complete
write_evidence true success
printf '{"candidate_sha":"%s","image_digest":"%s","evidence":"%s"}\n' \
	"$CANDIDATE_SHA" "$IMAGE_DIGEST" "$EVIDENCE_PATH"
