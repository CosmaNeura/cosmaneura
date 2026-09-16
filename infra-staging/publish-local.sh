#!/usr/bin/env bash

# Publish one reviewed, credential-free local build to the existing staging ACR.
# This script never deploys, migrates, writes `latest`, retries, or targets production.
# shellcheck disable=SC2016 # jq programs intentionally use literal $variables.

set -euo pipefail
umask 077

if [[ $# -ne 4 ]]; then
	echo "Usage: $0 BUILD_PLAN EXPECTED_PLAN_SHA256 CONTROLLER_RESULT EXPECTED_CONTROLLER_RESULT_SHA256" >&2
	exit 2
fi

BUILD_PLAN=$1
EXPECTED_PLAN_SHA256=$2
CONTROLLER_RESULT=$3
EXPECTED_CONTROLLER_RESULT_SHA256=$4
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"

PYTHON=/opt/homebrew/bin/python3
AZ=/opt/homebrew/bin/az
AZURE_CLI_PYTHON=/opt/homebrew/Cellar/azure-cli/2.89.1/libexec/bin/python
DOCKER=/opt/homebrew/Cellar/docker/29.7.2/bin/docker
JQ=/usr/bin/jq
export PATH=/opt/homebrew/Cellar/gh/2.97.0/bin:/opt/homebrew/bin:/usr/bin:/bin

CANDIDATE_SHA=866bd4bc01e0e7be32c59eccdaf9dac3f495c7de
APPROVAL_PR_NUMBER=385
EXPECTED_SCHEMA_AFTER=9a2399212488
REGISTRY_NAME=cosmaneurastagingregistry
REGISTRY_HOST=cosmaneurastagingregistry.azurecr.io
REGISTRY_REPOSITORY=cosmaneura/cosmaneura
UNTAGGED_UPLOADER="$REPOSITORY_ROOT/scripts/staging_acr_untagged_upload.py"
BOUND_AZURE_CLI="$REPOSITORY_ROOT/scripts/staging_azure_cli_bound.py"
PINNED_SUBSCRIPTION_ID=bab2a709-1fcb-4c87-a9fc-802f7477ec13
PINNED_TENANT_ID=d51754d0-bba5-4810-820e-afac542844d6

EVIDENCE_PATH=""
ATTEMPT_PATH=""
PHASE=validate_inputs
PUBLICATION_STARTED=false
IMAGE_DIGEST=""
PLAN_CONTROLLER_SHA=""
RESULT_SHA256=""
METADATA_SHA256=""
IMAGE_ID=""
ARCHIVE_SHA256=""
PLAN_SOURCE_ARCHIVE_SHA256=""
PLAN_JSON=""
CONTROLLER_RESULT_JSON=""
BUILD_METADATA_JSON=""
RESULT_BYTES=""
METADATA_BYTES=""

bound_az() {
	local management_token_json registry_token_json status
	management_token_json=$("$AZ" account get-access-token \
		--subscription "$PINNED_SUBSCRIPTION_ID" \
		--resource https://management.azure.com/ \
		--only-show-errors --output json) || return
	if [[ "${1:-}" == "acr" && \
		( "${2:-}" == "login" || "${2:-}" == "manifest" || "${2:-}" == "repository" ) ]]; then
		registry_token_json=$("$AZ" account get-access-token \
			--subscription "$PINNED_SUBSCRIPTION_ID" \
			--resource https://containerregistry.azure.net \
			--only-show-errors --output json) || return
		if "$AZURE_CLI_PYTHON" -I -B "$BOUND_AZURE_CLI" "$@" \
			--subscription "$PINNED_SUBSCRIPTION_ID" \
			3< <(printf '%s' "$management_token_json") \
			4< <(printf '%s' "$registry_token_json"); then
			status=0
		else
			status=$?
		fi
		unset registry_token_json
	else
		if "$AZURE_CLI_PYTHON" -I -B "$BOUND_AZURE_CLI" "$@" \
			--subscription "$PINNED_SUBSCRIPTION_ID" \
			3< <(printf '%s' "$management_token_json"); then
			status=0
		else
			status=$?
		fi
	fi
	unset management_token_json
	return "$status"
}

verify_bound_azure_identity() {
	local management_token_json result
	management_token_json=$("$AZ" account get-access-token \
		--subscription "$PINNED_SUBSCRIPTION_ID" \
		--resource https://management.azure.com/ \
		--only-show-errors --output json) || return
	if result=$("$AZURE_CLI_PYTHON" -I -B "$BOUND_AZURE_CLI" --verify-only \
		3< <(printf '%s' "$management_token_json")); then
		unset management_token_json
	else
		unset management_token_json
		return 1
	fi
	printf '%s' "$result" | "$JQ" -e \
		--arg subscription "$PINNED_SUBSCRIPTION_ID" \
		--arg tenant "$PINNED_TENANT_ID" '
		type == "object" and .azure_principal_bound == true and
		.management_token_used_for_mutation == false and
		.subscription == $subscription and .tenant == $tenant' >/dev/null
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
    current = path.lstat()
    if any(getattr(before, field) != getattr(current, field) for field in fields):
        raise SystemExit(1)
    print(f"{digest.hexdigest()} {size}")
finally:
    os.close(fd)
PY
}

secure_json_capture() {
	"$PYTHON" -I -S -B - "$1" "$2" "$3" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value

path = Path(sys.argv[1])
maximum = int(sys.argv[2])
expected = sys.argv[3]
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
    chunks = []
    size = 0
    digest = hashlib.sha256()
    while chunk := os.read(fd, min(65536, maximum - size + 1)):
        size += len(chunk)
        if size > maximum:
            raise SystemExit(1)
        chunks.append(chunk)
        digest.update(chunk)
    raw = b"".join(chunks)
    fields = (
        "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
        "st_size", "st_mtime_ns", "st_ctime_ns",
    )
    identity = tuple(getattr(before, field) for field in fields)
    if size != before.st_size:
        raise SystemExit(1)
    if any(tuple(getattr(info, field) for field in fields) != identity
           for info in (os.fstat(fd), path.lstat())):
        raise SystemExit(1)
    observed = digest.hexdigest()
    if expected != "-" and observed != expected:
        raise SystemExit(1)
    value = json.loads(raw, object_pairs_hook=unique)
    if type(value) is not dict:
        raise SystemExit(1)
    print(json.dumps(
        {"bytes": size, "sha256": observed, "value": value},
        sort_keys=True, separators=(",", ":"),
    ))
finally:
    os.close(fd)
PY
}

verify_founder_source() {
	local canonical_root origin dirty controller_sha source_result expected_identity_sha256
	canonical_root=$(/usr/bin/git -C "$REPOSITORY_ROOT" rev-parse --show-toplevel)
	origin=$(/usr/bin/git -C "$REPOSITORY_ROOT" remote get-url origin)
	dirty=$(/usr/bin/git -C "$REPOSITORY_ROOT" status --porcelain=v1 --untracked-files=all)
	if [[ "$canonical_root" != "$REPOSITORY_ROOT" || \
		"$origin" != "https://github.com/CosmaNeura/cosmaneura.git" || -n "$dirty" ]]; then
		echo "Local publication requires the clean canonical repository." >&2
		return 1
	fi
	controller_sha=$(/usr/bin/git -C "$REPOSITORY_ROOT" rev-parse HEAD)
	if [[ "$controller_sha" != "$PLAN_CONTROLLER_SHA" ]]; then
		echo "Local publication controller does not match the reviewed build plan." >&2
		return 1
	fi
	expected_identity_sha256=$(printf '%s' "$PLAN_JSON" | "$JQ" -er \
		'.github_user_sha256 | select(test("^[0-9a-f]{64}$"))')
	source_result=$(cd "$REPOSITORY_ROOT" && \
		"$PYTHON" -I -S -B scripts/staging_build_controller.py \
			--verify-source "$BUILD_PLAN" "$EXPECTED_PLAN_SHA256")
	printf '%s' "$source_result" | "$JQ" -e \
		--arg candidate "$CANDIDATE_SHA" \
		--argjson approval_pr "$APPROVAL_PR_NUMBER" \
		--arg schema "$EXPECTED_SCHEMA_AFTER" \
		--arg controller "$PLAN_CONTROLLER_SHA" \
		--arg plan "$EXPECTED_PLAN_SHA256" \
		--arg identity "$expected_identity_sha256" \
		--arg source "$PLAN_SOURCE_ARCHIVE_SHA256" '
		type == "object" and .local_bindings_passed == true and
		.authenticated_source_checked == true and .build_started == false and
		.candidate_sha == $candidate and .approval_pr_number == $approval_pr and
		.schema_head == $schema and .controller_sha == $controller and
		.plan_sha256 == $plan and .github_user_sha256 == $identity and
		.source_archive_sha256 == $source and
		(.protection_sha256 | test("^[0-9a-f]{64}$"))' >/dev/null
}

verify_founder_azure_identity() {
	if [[ -n "${AZURE_CLIENT_ID:-}" || -n "${AZURE_TENANT_ID:-}" || \
		-n "${AZURE_SUBSCRIPTION_ID:-}" || -n "${AZURE_CLIENT_SECRET:-}" || \
		-n "${AZURE_CLIENT_CERTIFICATE_PATH:-}" ]]; then
		echo "Local publication refuses configured service-principal inputs." >&2
		return 1
	fi
	if ! verify_bound_azure_identity; then
		echo "Failed to bind the exact founder token for staging publication." >&2
		return 1
	fi
}

remote_tag_absent() {
	local tags
	tags=$(bound_az acr repository show-tags \
		--name "$REGISTRY_NAME" \
		--repository "$REGISTRY_REPOSITORY" \
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
		--arg controller_result_sha256 "$RESULT_SHA256" \
		--arg build_metadata_sha256 "$METADATA_SHA256" \
		--arg image_id "$IMAGE_ID" \
		--arg archive_sha256 "$ARCHIVE_SHA256" \
		--arg image_digest "$IMAGE_DIGEST" \
		--arg image_reference "$REGISTRY_HOST/$REGISTRY_REPOSITORY${IMAGE_DIGEST:+@$IMAGE_DIGEST}" \
		--argjson complete "$complete" \
		--argjson publication_started "$PUBLICATION_STARTED" \
		'{schema_version:1,recorded_at:$recorded_at,complete:$complete,outcome:$outcome,
		  phase:$phase,candidate_sha:$candidate_sha,controller_sha:$controller_sha,
		  build_plan_sha256:$build_plan_sha256,
		  controller_result_sha256:$controller_result_sha256,
		  build_metadata_sha256:$build_metadata_sha256,image_id:$image_id,
		  archive_sha256:$archive_sha256,publication_started:$publication_started,
		  image_digest:(if $image_digest == "" then null else $image_digest end),
		  image_reference:(if $image_digest == "" then null else $image_reference end),
		  candidate_tag_creation_mode:"acr-import-no-force",
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
directory = os.open(os.path.dirname(path), os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
' "$EVIDENCE_PATH"
}

write_attempt() {
	[[ -n "$ATTEMPT_PATH" && ! -e "$ATTEMPT_PATH" ]] || return 1
	"$JQ" -cn \
		--arg started_at "$(/bin/date -u +%FT%TZ)" \
		--arg candidate_sha "$CANDIDATE_SHA" \
		--arg controller_sha "$PLAN_CONTROLLER_SHA" \
		--arg build_plan_sha256 "$EXPECTED_PLAN_SHA256" \
		--arg controller_result_sha256 "$RESULT_SHA256" \
		--arg build_metadata_sha256 "$METADATA_SHA256" \
		--arg archive_sha256 "$ARCHIVE_SHA256" \
		'{schema_version:1,started_at:$started_at,candidate_sha:$candidate_sha,
		  controller_sha:$controller_sha,build_plan_sha256:$build_plan_sha256,
		  controller_result_sha256:$controller_result_sha256,
		  build_metadata_sha256:$build_metadata_sha256,archive_sha256:$archive_sha256,
		  candidate_tag_creation_mode:"acr-import-no-force",
		  durable_before_first_registry_write:true,automatic_retry_allowed:false}' |
		"$PYTHON" -I -S -B -c '
import os
import sys
path = sys.argv[1]
raw = sys.stdin.buffer.read()
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
try:
    with os.fdopen(fd, "wb", closefd=False) as stream:
        if stream.write(raw) != len(raw):
            raise OSError("short attempt write")
        stream.flush()
        os.fsync(stream.fileno())
finally:
    os.close(fd)
directory = os.open(os.path.dirname(path), os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
' "$ATTEMPT_PATH"
}

cleanup() {
	local status=$?
	set +e
	unset ACR_LOGIN_JSON PLAN_JSON CONTROLLER_RESULT_JSON BUILD_METADATA_JSON
	if [[ $status -ne 0 ]]; then
		write_evidence false failed
	fi
	exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

for tool in "$PYTHON" "$AZ" "$AZURE_CLI_PYTHON" "$DOCKER" "$JQ" \
	"$UNTAGGED_UPLOADER" "$BOUND_AZURE_CLI"; do
	[[ -x "$tool" ]] || { echo "Required publication tool is unavailable." >&2; exit 1; }
done
if [[ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" || -n "${RECALL_WEBHOOK_SECRET:-}" || \
	-n "${DOCKER_AUTH_CONFIG:-}" ]]; then
	echo "Local publication refuses deployment or ambient registry credentials." >&2
	exit 1
fi
unset GH_TOKEN GITHUB_TOKEN DOCKER_TLS_VERIFY DOCKER_CERT_PATH
[[ "$EXPECTED_PLAN_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "Invalid build-plan digest." >&2; exit 1; }
[[ "$EXPECTED_CONTROLLER_RESULT_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
	echo "Invalid expected controller-result digest." >&2
	exit 1
}

PLAN_CAPTURE=$(secure_json_capture "$BUILD_PLAN" 65536 "$EXPECTED_PLAN_SHA256")
printf '%s' "$PLAN_CAPTURE" | "$JQ" -e \
	--arg expected "$EXPECTED_PLAN_SHA256" '
	type == "object" and keys == ["bytes","sha256","value"] and
	.sha256 == $expected and (.bytes | type == "number" and . > 0) and
	(.value | type == "object")' >/dev/null
PLAN_JSON=$(printf '%s' "$PLAN_CAPTURE" | "$JQ" -cer '.value')
unset PLAN_CAPTURE
PLAN_CONTROLLER_SHA=$(printf '%s' "$PLAN_JSON" | "$JQ" -er \
	'.controller_sha | select(test("^[0-9a-f]{40}$"))')
PLAN_SOURCE_ARCHIVE_SHA256=$(printf '%s' "$PLAN_JSON" | "$JQ" -er \
	'.source_archive_sha256 | select(test("^[0-9a-f]{64}$"))')
[[ "$(printf '%s' "$PLAN_JSON" | "$JQ" -er '.candidate_sha')" == "$CANDIDATE_SHA" ]] || exit 1
[[ "$(printf '%s' "$PLAN_JSON" | "$JQ" -er '.candidate_pr')" == "$APPROVAL_PR_NUMBER" ]] || exit 1

RESULT_CAPTURE=$(secure_json_capture \
	"$CONTROLLER_RESULT" 65536 "$EXPECTED_CONTROLLER_RESULT_SHA256")
RESULT_SHA256=$(printf '%s' "$RESULT_CAPTURE" | "$JQ" -er '.sha256')
RESULT_BYTES=$(printf '%s' "$RESULT_CAPTURE" | "$JQ" -er \
	'.bytes | select(type == "number" and . > 0)')
CONTROLLER_RESULT_JSON=$(printf '%s' "$RESULT_CAPTURE" | "$JQ" -cer '.value')
unset RESULT_CAPTURE
[[ "$RESULT_SHA256" == "$EXPECTED_CONTROLLER_RESULT_SHA256" ]] || {
	echo "Controller result does not match the separately supplied expected digest." >&2
	exit 1
}
printf '%s\n%s\n' "$CONTROLLER_RESULT_JSON" "$PLAN_JSON" | "$JQ" -se \
	--arg candidate "$CANDIDATE_SHA" \
	--arg controller "$PLAN_CONTROLLER_SHA" \
	--arg plan_sha256 "$EXPECTED_PLAN_SHA256" \
	--arg source_sha256 "$PLAN_SOURCE_ARCHIVE_SHA256" '
	length == 2 and (.[0] as $result | .[1] as $plan |
	$result | type == "object" and keys == [
	  "actor","build_metadata","build_metadata_bytes","build_metadata_sha256",
	  "build_root","candidate_sha","complete",
	  "controller_sha","deployment_authorized","finished_at",
	  "frontend_configuration_sha256","image_archive","image_archive_bytes",
	  "image_archive_sha256","image_id","module_sha256s","plan_sha256",
	  "public_assets_sha256","publication_authorized","source_archive_sha256",
	  "started_at"
	] and $result.complete == true and $result.actor == "delegated_assistant" and
	$result.candidate_sha == $candidate and $result.controller_sha == $controller and
	$result.plan_sha256 == $plan_sha256 and $result.module_sha256s == $plan.module_sha256s and
	$result.publication_authorized == false and $result.deployment_authorized == false and
	$result.source_archive_sha256 == $source_sha256 and
	($result.frontend_configuration_sha256 | test("^[0-9a-f]{64}$")) and
	($result.public_assets_sha256 | test("^[0-9a-f]{64}$")) and
	($result.image_id | test("^sha256:[0-9a-f]{64}$")) and
	($result.image_archive_sha256 | test("^[0-9a-f]{64}$")) and
	($result.image_archive_bytes | type == "number" and . > 0 and . <= 17179869184) and
	($result.build_metadata_sha256 | test("^[0-9a-f]{64}$")) and
	($result.build_metadata_bytes | type == "number" and . > 0 and . <= 65536) and
	($result.started_at | type == "string") and
	($result.finished_at | type == "string"))' >/dev/null
BUILD_ROOT=$(printf '%s' "$CONTROLLER_RESULT_JSON" | "$JQ" -er '.build_root')
[[ "$BUILD_ROOT" =~ ^/private/tmp/cosmaneura-staging-build-[0-9a-f]{32}$ ]] || exit 1
[[ "$CONTROLLER_RESULT" == "$BUILD_ROOT/controller-result.json" ]] || exit 1
IMAGE_ID=$(printf '%s' "$CONTROLLER_RESULT_JSON" | "$JQ" -er \
	'.image_id | select(test("^sha256:[0-9a-f]{64}$"))')
IMAGE_ARCHIVE=$(printf '%s' "$CONTROLLER_RESULT_JSON" | "$JQ" -er '.image_archive')
ARCHIVE_SHA256=$(printf '%s' "$CONTROLLER_RESULT_JSON" | "$JQ" -er \
	'.image_archive_sha256 | select(test("^[0-9a-f]{64}$"))')
ARCHIVE_BYTES=$(printf '%s' "$CONTROLLER_RESULT_JSON" | "$JQ" -er \
	'.image_archive_bytes | select(type == "number" and . > 0 and . <= 17179869184)')
BUILD_METADATA=$(printf '%s' "$CONTROLLER_RESULT_JSON" | "$JQ" -er '.build_metadata')
METADATA_SHA256=$(printf '%s' "$CONTROLLER_RESULT_JSON" | "$JQ" -er \
	'.build_metadata_sha256 | select(test("^[0-9a-f]{64}$"))')
METADATA_BYTES=$(printf '%s' "$CONTROLLER_RESULT_JSON" | "$JQ" -er \
	'.build_metadata_bytes | select(type == "number" and . > 0 and . <= 65536)')
[[ "$IMAGE_ARCHIVE" == "$BUILD_ROOT/artifacts/application-image.tar" ]] || exit 1
[[ "$BUILD_METADATA" == "$BUILD_ROOT/artifacts/build-result.json" ]] || exit 1
EVIDENCE_PATH="$BUILD_ROOT/artifacts/publication-result.json"
ATTEMPT_PATH="$BUILD_ROOT/artifacts/publication-attempt.json"
[[ ! -e "$EVIDENCE_PATH" ]] || { echo "Publication evidence already exists; refusing a retry." >&2; exit 1; }
[[ ! -e "$ATTEMPT_PATH" ]] || { echo "Publication attempt already exists; refusing a retry." >&2; exit 1; }

read -r observed_archive_sha256 observed_archive_bytes < <(secure_file_fingerprint "$IMAGE_ARCHIVE" 17179869184)
[[ "$observed_archive_sha256" == "$ARCHIVE_SHA256" && "$observed_archive_bytes" == "$ARCHIVE_BYTES" ]] || exit 1
METADATA_CAPTURE=$(secure_json_capture "$BUILD_METADATA" 65536 "$METADATA_SHA256")
printf '%s' "$METADATA_CAPTURE" | "$JQ" -e \
	--arg sha256 "$METADATA_SHA256" \
	--argjson bytes "$METADATA_BYTES" '
	.sha256 == $sha256 and .bytes == $bytes and (.value | type == "object")' >/dev/null
BUILD_METADATA_JSON=$(printf '%s' "$METADATA_CAPTURE" | "$JQ" -cer '.value')
unset METADATA_CAPTURE
printf '%s\n%s\n%s\n' \
	"$BUILD_METADATA_JSON" "$CONTROLLER_RESULT_JSON" "$PLAN_JSON" | "$JQ" -se '
	length == 3 and (.[0] as $metadata | .[1] as $result | .[2] as $plan |
	$metadata | type == "object" and keys == [
	  "archive_bytes","archive_sha256","candidate_sha","deployment_authorized",
	  "engine_id_sha256","frontend_configuration_sha256","image_id",
	  "local_children_closed","owned_extraction_container_removed",
	  "public_assets_sha256","publication_authorized","schema_version",
	  "source_archive_sha256"
	] and $metadata.schema_version == 1 and
	$metadata.candidate_sha == $result.candidate_sha and
	$metadata.source_archive_sha256 == $result.source_archive_sha256 and
	$metadata.frontend_configuration_sha256 == $result.frontend_configuration_sha256 and
	$metadata.image_id == $result.image_id and
	$metadata.archive_sha256 == $result.image_archive_sha256 and
	$metadata.archive_bytes == $result.image_archive_bytes and
	$metadata.public_assets_sha256 == $result.public_assets_sha256 and
	$metadata.engine_id_sha256 == $plan.engine.id_sha256 and
	$metadata.local_children_closed == true and
	$metadata.owned_extraction_container_removed == true and
	$metadata.publication_authorized == false and
	$metadata.deployment_authorized == false)' >/dev/null

PHASE=revalidate_build_plan
revalidate_build_plan

PHASE=verify_source_and_identity
verify_founder_source
verify_founder_azure_identity

PHASE=load_and_verify_archive
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
remote_tag_absent || { echo "The remote immutable candidate tag is not absent." >&2; exit 1; }

PHASE=final_prepublication_verification
revalidate_build_plan
verify_founder_source
verify_founder_azure_identity
read -r final_result_sha256 final_result_bytes < <(
	secure_file_fingerprint "$CONTROLLER_RESULT" 65536
)
[[ "$final_result_sha256" == "$RESULT_SHA256" && \
	"$final_result_bytes" == "$RESULT_BYTES" ]] || exit 1
read -r final_metadata_sha256 final_metadata_bytes < <(
	secure_file_fingerprint "$BUILD_METADATA" 65536
)
[[ "$final_metadata_sha256" == "$METADATA_SHA256" && \
	"$final_metadata_bytes" == "$METADATA_BYTES" ]] || exit 1
read -r final_archive_sha256 final_archive_bytes < <(secure_file_fingerprint "$IMAGE_ARCHIVE" 17179869184)
[[ "$final_archive_sha256" == "$ARCHIVE_SHA256" && "$final_archive_bytes" == "$ARCHIVE_BYTES" ]] || exit 1
[[ "$("$DOCKER" image inspect --format '{{.Id}}' "$IMAGE_ID")" == "$IMAGE_ID" ]] || exit 1
remote_tag_absent || { echo "The remote immutable candidate tag is not absent." >&2; exit 1; }

PHASE=durable_publication_attempt
write_attempt
PUBLICATION_STARTED=true

PHASE=content_addressed_upload
ACR_LOGIN_JSON=$(bound_az acr login --name "$REGISTRY_NAME" \
	--expose-token \
	--only-show-errors --output json)
UPLOAD_RESULT=$(printf '%s' "$ACR_LOGIN_JSON" | \
	"$PYTHON" -I -S -B "$UNTAGGED_UPLOADER" \
		"$IMAGE_ARCHIVE" "$ARCHIVE_SHA256" "$IMAGE_ID")
unset ACR_LOGIN_JSON
IMAGE_DIGEST=$(printf '%s' "$UPLOAD_RESULT" | "$JQ" -er \
	--arg archive "$ARCHIVE_SHA256" \
	--arg candidate "$CANDIDATE_SHA" '
	if .schema_version == 1 and .archive_sha256 == $archive and
	 .candidate_sha == $candidate and .registry_host == "cosmaneurastagingregistry.azurecr.io" and
	 .repository == "cosmaneura/cosmaneura" and .tag_created == false and
	 .tag_overwrite_possible == false and (.layer_count | type == "number" and . > 0) and
	 (.manifest_digest | test("^sha256:[0-9a-f]{64}$"))
	then .manifest_digest else error("untagged upload evidence mismatch") end')
unset UPLOAD_RESULT

PHASE=create_candidate_tag_no_force
bound_az acr import --name "$REGISTRY_NAME" \
	--registry "$REGISTRY_NAME" \
	--source "$REGISTRY_REPOSITORY@$IMAGE_DIGEST" \
	--image "$REGISTRY_REPOSITORY:$CANDIDATE_SHA" \
	--only-show-errors --output none

PHASE=verify_remote_digest
REMOTE_DIGEST=$(bound_az acr manifest show-metadata \
	--registry "$REGISTRY_NAME" \
	--name "$REGISTRY_REPOSITORY:$CANDIDATE_SHA" \
	--query digest --output tsv)
[[ "$REMOTE_DIGEST" == "$IMAGE_DIGEST" ]] || {
	echo "The registry digest does not match the pushed candidate." >&2
	exit 1
}
PHASE=lock_and_reverify_candidate_tag
bound_az acr repository update --name "$REGISTRY_NAME" \
	--image "$REGISTRY_REPOSITORY:$CANDIDATE_SHA" \
	--write-enabled false --delete-enabled false \
	--only-show-errors --output none
TAG_ATTRIBUTES=$(bound_az acr repository show --name "$REGISTRY_NAME" \
	--image "$REGISTRY_REPOSITORY:$CANDIDATE_SHA" \
	--output json)
printf '%s' "$TAG_ATTRIBUTES" | "$JQ" -e --arg digest "$IMAGE_DIGEST" \
	'.digest == $digest and
	 .changeableAttributes.writeEnabled == false and
	 .changeableAttributes.deleteEnabled == false and
	 .changeableAttributes.readEnabled == true' >/dev/null
unset TAG_ATTRIBUTES

PHASE=complete
write_evidence true success
printf '{"candidate_sha":"%s","image_digest":"%s","evidence":"%s"}\n' \
	"$CANDIDATE_SHA" "$IMAGE_DIGEST" "$EVIDENCE_PATH"
