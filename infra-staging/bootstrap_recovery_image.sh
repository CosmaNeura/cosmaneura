#!/usr/bin/env bash
# One-time bounded bootstrap: lock the exact image, then copy three exact revisions.
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
POLICY="$SCRIPT_DIR/recovery_image_bootstrap_policy.json"
VALIDATOR="$REPOSITORY_ROOT/scripts/validate_staging_recovery_image_bootstrap.py"
RECEIPT_DIR="$REPOSITORY_ROOT/evidence"
RECEIPT="$RECEIPT_DIR/staging-recovery-image-bootstrap-receipt.json"
MODE="${BOOTSTRAP_MODE:-}"
CAPTURE_RUN_ID="${CAPTURE_RUN_ID:-}"
CAPTURE_RECEIPT_SHA256="${CAPTURE_RECEIPT_SHA256:-}"
TYPED_CONFIRMATION="${TYPED_CONFIRMATION:-}"
for required in GITHUB_REPOSITORY GITHUB_REF GITHUB_REF_PROTECTED GITHUB_SHA  GITHUB_RUN_ID GITHUB_RUN_ATTEMPT GITHUB_EVENT_NAME GITHUB_ACTOR  GITHUB_TRIGGERING_ACTOR GH_TOKEN AZURE_CLIENT_ID AZURE_TENANT_ID  AZURE_SUBSCRIPTION_ID; do
	if [[ -z "${!required:-}" ]]; then
		echo "Missing required workflow context: $required" >&2
		exit 2
	fi
done
python3 "$VALIDATOR" validate-policy --policy "$POLICY" >/dev/null
mkdir -p "$RECEIPT_DIR"
WORK_DIR=$(mktemp -d "${RUNNER_TEMP:-/tmp}/staging-recovery-image-bootstrap.XXXXXX")
JOURNAL="$WORK_DIR/journal.json"
printf '%s\n' '{"registry":{"tag_lock":"not_attempted","manifest_lock":"not_attempted"},"workloads":{"cosmaneura-staging-worker":"not_attempted","cosmaneura-staging-scheduler":"not_attempted","cosmaneura-staging-app":"not_attempted"}}' >"$JOURNAL"
FINAL_STATUS="failed"
FINAL_PHASE="initialization"
FINAL_EVIDENCE=""
update_journal() {
	local expression=$1
	jq "$expression" "$JOURNAL" >"$JOURNAL.next"
	mv "$JOURNAL.next" "$JOURNAL"
}
emit_terminal_receipt() {
	local receipt_args=(
		emit-receipt --policy "$POLICY" --mode "$MODE" --status "$FINAL_STATUS"
		--phase "$FINAL_PHASE" --source-sha "$GITHUB_SHA"
		--run-id "$GITHUB_RUN_ID" --run-attempt "$GITHUB_RUN_ATTEMPT"
		--actor "$GITHUB_ACTOR" --triggering-actor "$GITHUB_TRIGGERING_ACTOR"
		--journal "$JOURNAL" --output "$RECEIPT"
	)
	if [[ -n "$FINAL_EVIDENCE" && -f "$FINAL_EVIDENCE" ]]; then
		receipt_args+=(--evidence "$FINAL_EVIDENCE")
	fi
	if [[ "$MODE" == "pin" && "$CAPTURE_RUN_ID" =~ ^[1-9][0-9]*$ && "$CAPTURE_RECEIPT_SHA256" =~ ^sha256:[0-9a-f]{64}$ ]]; then
		receipt_args+=(--capture-run-id "$CAPTURE_RUN_ID" --capture-sha256 "$CAPTURE_RECEIPT_SHA256")
	fi
	python3 "$VALIDATOR" "${receipt_args[@]}"
}
finish() {
	local result=$?
	trap - EXIT
	set +e
	emit_terminal_receipt
	local receipt_result=$?
	if [[ -f "$RECEIPT" ]]; then
		printf 'bootstrap_receipt_sha256=sha256:%s\n' "$(shasum -a 256 "$RECEIPT" | awk '{print $1}')"
	fi
	rm -rf "$WORK_DIR"
	if [[ $result -eq 0 && $receipt_result -eq 0 &&  ("$FINAL_STATUS" == "captured" || "$FINAL_STATUS" == "complete") ]]; then
		exit 0
	fi
	exit 1
}
trap finish EXIT
emit_terminal_receipt
policy_value() {
	jq -er "$1" "$POLICY"
}
REGISTRY_NAME=$(policy_value '.registry.name')
REGISTRY_SERVER=$(policy_value '.registry.login_server')
REGISTRY_REPOSITORY=$(policy_value '.registry.repository')
SOURCE_TAG=$(policy_value '.registry.source_tag')
MANIFEST_DIGEST=$(policy_value '.registry.manifest_digest')
CONFIG_DIGEST=$(policy_value '.registry.config_digest')
RESOURCE_GROUP=$(policy_value '.azure.resource_group')
IDENTITY_NAME=$(policy_value '.azure.identity_name')
EXPECTED_CLIENT_ID=$(policy_value '.azure.client_id')
EXPECTED_PRINCIPAL_ID=$(policy_value '.azure.principal_id')
EXPECTED_TENANT_ID=$(policy_value '.azure.tenant_id')
EXPECTED_SUBSCRIPTION_ID=$(policy_value '.azure.subscription_id')
PINNED_SUFFIX=$(policy_value '.pinned_revision_suffix')
EXPECTED_CONFIRMATION=$(policy_value '.confirmation')
HISTORICAL_RUN_ID=$(policy_value '.historical_publication.run.id')
HISTORICAL_JOB_ID=$(policy_value '.historical_publication.job.id')
collect_effective_assignments() {
	az role assignment list --assignee-object-id "$EXPECTED_PRINCIPAL_ID"  --include-groups --include-inherited --all  --subscription "$EXPECTED_SUBSCRIPTION_ID" --fill-principal-name false  --fill-role-definition-name false  --query '[].{principalId:principalId,principalType:principalType,roleDefinitionId:roleDefinitionId,scope:scope}' -o json
}
collect_environment_policy() {
	local environment branches
	environment=$(gh api "repos/$GITHUB_REPOSITORY/environments/Staging")
	branches=$(gh api  "repos/$GITHUB_REPOSITORY/environments/Staging/deployment-branch-policies?per_page=100")
	jq -cn --argjson environment "$environment" --argjson branches "$branches"  '{name:$environment.name,can_admins_bypass:$environment.can_admins_bypass,protection_rules:[$environment.protection_rules[]|{type}],deployment_branch_policy:$environment.deployment_branch_policy,total_count:$branches.total_count,branch_policies:[$branches.branch_policies[]|{name,type}]}'
}
verify_control_plane() {
	local subscriptions assignments environment
	[[ "$GITHUB_REPOSITORY" == "CosmaNeura/cosmaneura" ]]
	[[ "$GITHUB_REF" == "refs/heads/master" ]]
	[[ "$GITHUB_REF_PROTECTED" == "true" ]]
	[[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]]
	[[ "$GITHUB_ACTOR" == "JohnClem" ]]
	[[ "$GITHUB_TRIGGERING_ACTOR" == "JohnClem" ]]
	[[ "$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)" == "$GITHUB_SHA" ]]
	[[ "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/master" --jq .object.sha)" == "$GITHUB_SHA" ]]
	[[ "$AZURE_CLIENT_ID" == "$EXPECTED_CLIENT_ID" ]]
	[[ "$AZURE_TENANT_ID" == "$EXPECTED_TENANT_ID" ]]
	[[ "$AZURE_SUBSCRIPTION_ID" == "$EXPECTED_SUBSCRIPTION_ID" ]]
	environment=$(collect_environment_policy)
	jq -e '.name == "Staging" and .can_admins_bypass == false and .protection_rules == [{type:"branch_policy"}] and .deployment_branch_policy == {protected_branches:false,custom_branch_policies:true} and .total_count == 1 and .branch_policies == [{name:"master",type:"branch"}]'  <<<"$environment" >/dev/null
	az account show  --query '{subscriptionId:id,tenantId:tenantId,userName:user.name,userType:user.type}'  -o json | jq -e  --arg subscription "$EXPECTED_SUBSCRIPTION_ID"  --arg tenant "$EXPECTED_TENANT_ID" --arg client "$EXPECTED_CLIENT_ID"  '.subscriptionId == $subscription and .tenantId == $tenant and
		 .userName == $client and .userType == "servicePrincipal"' >/dev/null
	az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP"  --query '{clientId:clientId,principalId:principalId,tenantId:tenantId}' -o json |
		jq -e --arg client "$EXPECTED_CLIENT_ID" --arg principal "$EXPECTED_PRINCIPAL_ID"  --arg tenant "$EXPECTED_TENANT_ID"  '.clientId == $client and .principalId == $principal and .tenantId == $tenant' >/dev/null
	az identity federated-credential list --identity-name "$IDENTITY_NAME"  --resource-group "$RESOURCE_GROUP"  --query '[].{issuer:issuer,subject:subject,audiences:audiences}' -o json |
		jq -e 'length == 1 and .[0] == {
			issuer:"https://token.actions.githubusercontent.com",
			subject:"repo:CosmaNeura/cosmaneura:environment:staging",
			audiences:["api://AzureADTokenExchange"]
		}' >/dev/null
	subscriptions=$(az account list --refresh --all  --query '[].{subscriptionId:id,tenantId:tenantId,state:state}' -o json)
	jq -e --argjson actual "$subscriptions"  '($actual | sort_by(.subscriptionId)) == (.azure.enabled_subscriptions | sort_by(.subscriptionId))'  "$POLICY" >/dev/null
	assignments=$(collect_effective_assignments)
	jq -e --argjson actual "$assignments"  'def normalized: map({principalId:(.principalId|ascii_downcase),principalType,roleDefinitionId:(.roleDefinitionId|ascii_downcase),scope:(.scope|ascii_downcase)})|sort_by(.roleDefinitionId,.scope); ($actual|normalized) == (.azure.effective_role_assignments|normalized)'  "$POLICY" >/dev/null
}
collect_workload() {
	local name=$1 output=$2 app_json revisions_json
	app_json=$(az containerapp show --resource-group "$RESOURCE_GROUP" --name "$name"  --query '{name:name,id:id,location:location,identity:identity,tags:tags,properties:{managedEnvironmentId:properties.managedEnvironmentId,configuration:{activeRevisionsMode:properties.configuration.activeRevisionsMode,maxInactiveRevisions:properties.configuration.maxInactiveRevisions,dapr:properties.configuration.dapr,ingress:properties.configuration.ingress,registries:properties.configuration.registries[].{server:server,identity:identity,passwordSecretRef:passwordSecretRef}},template:properties.template,latestRevisionName:properties.latestRevisionName,latestReadyRevisionName:properties.latestReadyRevisionName,provisioningState:properties.provisioningState,runningStatus:properties.runningStatus}}'  -o json)
	revisions_json=$(az containerapp revision list --resource-group "$RESOURCE_GROUP"  --name "$name"  --query '[].{name:name,properties:{active:properties.active,healthState:properties.healthState,provisioningState:properties.provisioningState,runningState:properties.runningState,trafficWeight:properties.trafficWeight,template:properties.template}}'  -o json)
	jq -n --arg name "$name" --argjson app "$app_json"  --argjson revisions "$revisions_json" '{name:$name,app:$app,revisions:$revisions}' >"$output"
}
collect_bundle() {
	local bundle=$1
	mkdir -p "$bundle"
	verify_control_plane
	local current_master local_head
	current_master=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/master" --jq .object.sha)
	local_head=$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)
	collect_environment_policy >"$bundle/environment-policy.json"
	jq -n --arg repository "$GITHUB_REPOSITORY" --arg ref "$GITHUB_REF"  --arg protected "$GITHUB_REF_PROTECTED" --arg event "$GITHUB_EVENT_NAME"  --arg actor "$GITHUB_ACTOR" --arg triggering "$GITHUB_TRIGGERING_ACTOR"  --arg sha "$GITHUB_SHA" --arg local "$local_head" --arg master "$current_master"  --slurpfile environment "$bundle/environment-policy.json"  '{repository:$repository,ref:$ref,ref_protected:($protected == "true"),event_name:$event,actor:$actor,triggering_actor:$triggering,workflow_sha:$sha,local_head:$local,current_master:$master,environment_policy:$environment[0]}'  >"$bundle/source.json"
	az account show  --query '{subscriptionId:id,tenantId:tenantId,userName:user.name,userType:user.type}'  -o json >"$bundle/account.json"
	az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP"  --query '{clientId:clientId,principalId:principalId,tenantId:tenantId,id:id}'  -o json >"$bundle/identity.json"
	az identity federated-credential list --identity-name "$IDENTITY_NAME"  --resource-group "$RESOURCE_GROUP"  --query '[].{issuer:issuer,subject:subject,audiences:audiences}'  -o json >"$bundle/federated.json"
	az account list --refresh --all  --query '[].{subscriptionId:id,tenantId:tenantId,state:state}'  -o json >"$bundle/subscriptions.json"
	collect_effective_assignments >"$bundle/role-assignments.json"
	gh api "repos/$GITHUB_REPOSITORY/actions/runs/$HISTORICAL_RUN_ID" |
		jq '{id,run_attempt,event,status,conclusion,head_branch,head_sha,actor:.actor.login,triggering_actor:.triggering_actor.login,path,created_at,updated_at}'  >"$bundle/historical-run.json"
	gh api "repos/$GITHUB_REPOSITORY/actions/jobs/$HISTORICAL_JOB_ID" |
		jq '{id,run_id,run_attempt,name,status,conclusion,head_sha,started_at,completed_at,runner_group_name}'  >"$bundle/historical-job.json"
	gh api --allow-escape-sequences  "repos/$GITHUB_REPOSITORY/actions/jobs/$HISTORICAL_JOB_ID/logs"  >"$bundle/historical-job.log"
	az acr show --name "$REGISTRY_NAME"  --query '{id:id,name:name,loginServer:loginServer,adminUserEnabled:adminUserEnabled,policies:policies}'  -o json >"$bundle/registry.json"
	registry_id=$(jq -er .id "$bundle/registry.json")
	az monitor diagnostic-settings list --resource "$registry_id" --query value -o json  >"$bundle/registry-diagnostics.json"
	az acr repository show-tags --name "$REGISTRY_NAME" --repository "$REGISTRY_REPOSITORY"  --detail --query "[?name=='$SOURCE_TAG']" -o json >"$bundle/tag.json"
	az acr manifest show-metadata --registry "$REGISTRY_NAME"  --name "$REGISTRY_REPOSITORY@$MANIFEST_DIGEST" -o json  >"$bundle/manifest-metadata.json"
	local refresh_token access_token token_header
	refresh_token=$(az acr login --name "$REGISTRY_NAME" --expose-token  --query accessToken -o tsv 2>/dev/null)
	access_token=$(
		printf '%s' "$refresh_token" |
			curl -fsS -X POST "https://$REGISTRY_SERVER/oauth2/token"  --data-urlencode grant_type=refresh_token --data-urlencode "service=$REGISTRY_SERVER"  --data-urlencode "scope=repository:$REGISTRY_REPOSITORY:pull"  --data-urlencode 'refresh_token@-' | jq -er .access_token
	)
	token_header="$bundle/acr-auth.header"
	printf 'Authorization: Bearer %s\n' "$access_token" >"$token_header"
	curl -fsS -H "@$token_header"  -H 'Accept: application/vnd.docker.distribution.manifest.v2+json'  "https://$REGISTRY_SERVER/v2/$REGISTRY_REPOSITORY/manifests/$MANIFEST_DIGEST"  -o "$bundle/manifest.json"
	curl -L -fsS -H "@$token_header"  -H 'Accept: application/vnd.docker.container.image.v1+json'  "https://$REGISTRY_SERVER/v2/$REGISTRY_REPOSITORY/blobs/$CONFIG_DIGEST"  -o "$bundle/config.json"
	rm -f "$token_header"
	unset refresh_token access_token
	for name in cosmaneura-staging-worker cosmaneura-staging-scheduler cosmaneura-staging-app; do
		collect_workload "$name" "$bundle/$name.json"
	done
	jq -s '.' "$bundle/cosmaneura-staging-worker.json"  "$bundle/cosmaneura-staging-scheduler.json" "$bundle/cosmaneura-staging-app.json"  >"$bundle/workloads.json"
	jq -n --slurpfile source "$bundle/source.json"  --slurpfile account "$bundle/account.json" --slurpfile identity "$bundle/identity.json"  --slurpfile federated "$bundle/federated.json"  --slurpfile subscriptions "$bundle/subscriptions.json"  --slurpfile assignments "$bundle/role-assignments.json"  --slurpfile run "$bundle/historical-run.json" --slurpfile job "$bundle/historical-job.json"  --slurpfile registry "$bundle/registry.json"  --slurpfile diagnostics "$bundle/registry-diagnostics.json"  --slurpfile tag "$bundle/tag.json" --slurpfile manifest "$bundle/manifest-metadata.json"  --slurpfile workloads "$bundle/workloads.json"  '{source:$source[0],azure:{account:$account[0],identity:$identity[0],federated_credentials:$federated[0],enabled_subscriptions:$subscriptions[0],effective_role_assignments:$assignments[0]},historical:{run:$run[0],job:$job[0]},registry:{resource:$registry[0],diagnostic_settings:$diagnostics[0],tag_metadata:$tag[0],manifest_metadata:$manifest[0]},workloads:$workloads[0]}'  >"$bundle/observation.json"
	python3 "$VALIDATOR" validate-capture --policy "$POLICY"  --observation "$bundle/observation.json" --job-log "$bundle/historical-job.log"  --manifest "$bundle/manifest.json" --config "$bundle/config.json"  --output "$bundle/validated.json"
}
revalidate_progress() {
	local expected=$1 bundle="$WORK_DIR/pre-$2"
	collect_bundle "$bundle"
	python3 "$VALIDATOR" validate-transition --policy "$POLICY" --before "$CURRENT_BASELINE" --after "$bundle/validated.json" --expected-progress "$expected"
	FINAL_EVIDENCE="$bundle/validated.json"
	CURRENT_BASELINE="$bundle/validated.json"
}
download_capture_receipt() {
	local target=$1 run_json=$2 artifacts_json artifact_ids matches=0
	gh api "repos/$GITHUB_REPOSITORY/actions/runs/$CAPTURE_RUN_ID" |
		jq '{id,run_attempt,event,status,conclusion,head_branch,head_sha,actor:.actor.login,triggering_actor:.triggering_actor.login,path}'  >"$run_json"
	artifacts_json=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$CAPTURE_RUN_ID/artifacts?per_page=100")
	artifact_ids=$(jq -r --arg prefix "staging-recovery-image-bootstrap-receipt-$CAPTURE_RUN_ID-" '
		[.artifacts[] | select(.expired == false and (.name | startswith($prefix)))] |
		if length > 0 then .[].id else error("capture receipt artifact unavailable") end'  <<<"$artifacts_json")
	while IFS= read -r artifact_id; do
		[[ "$artifact_id" =~ ^[1-9][0-9]*$ ]] || continue
		candidate_dir="$WORK_DIR/capture-$artifact_id"
		mkdir "$candidate_dir"
		gh api --allow-escape-sequences  "repos/$GITHUB_REPOSITORY/actions/artifacts/$artifact_id/zip"  >"$candidate_dir/receipt.zip"
		if python3 "$VALIDATOR" extract-receipt --archive "$candidate_dir/receipt.zip"  --output "$candidate_dir/receipt.json"; then
			candidate_sha="sha256:$(shasum -a 256 "$candidate_dir/receipt.json" | awk '{print $1}')"
			if [[ "$candidate_sha" == "$CAPTURE_RECEIPT_SHA256" ]]; then
				cp "$candidate_dir/receipt.json" "$target"
				matches=$((matches + 1))
			fi
		fi
	done <<<"$artifact_ids"
	[[ $matches -eq 1 ]]
}
validate_capture_authorization() {
	python3 "$VALIDATOR" validate-receipt --policy "$POLICY" --receipt "$CAPTURE_RECEIPT" --capture-run "$CAPTURE_RUN" --expected-run-id "$CAPTURE_RUN_ID" --expected-sha256 "$CAPTURE_RECEIPT_SHA256" --current-sha "$GITHUB_SHA"
}
wait_for_pinned_workload() {
	local name=$1 baseline=$2 output=$3
	for _attempt in $(seq 1 60); do
		collect_workload "$name" "$output"
		if python3 "$VALIDATOR" validate-workload --policy "$POLICY"  --baseline "$baseline" --workload "$output" >/dev/null 2>&1; then
			return 0
		fi
		sleep 5
	done
	python3 "$VALIDATOR" validate-workload --policy "$POLICY"  --baseline "$baseline" --workload "$output"
}
if [[ "$MODE" != "capture" && "$MODE" != "pin" ]]; then
	echo "BOOTSTRAP_MODE must be capture or pin." >&2
	exit 2
fi
FINAL_PHASE="initial_observation"
INITIAL_BUNDLE="$WORK_DIR/initial"
collect_bundle "$INITIAL_BUNDLE"
FINAL_EVIDENCE="$INITIAL_BUNDLE/validated.json"
CURRENT_BASELINE="$INITIAL_BUNDLE/validated.json"
if [[ "$MODE" == "capture" ]]; then
	[[ -z "$CAPTURE_RUN_ID" && -z "$CAPTURE_RECEIPT_SHA256" && -z "$TYPED_CONFIRMATION" ]]
	FINAL_STATUS="captured"
	FINAL_PHASE="capture_validated"
	exit 0
fi
FINAL_PHASE="capture_receipt_authentication"
[[ "$CAPTURE_RUN_ID" =~ ^[1-9][0-9]*$ ]]
[[ "$CAPTURE_RECEIPT_SHA256" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$TYPED_CONFIRMATION" == "$EXPECTED_CONFIRMATION" ]]
CAPTURE_RECEIPT="$WORK_DIR/capture-receipt.json"
CAPTURE_RUN="$WORK_DIR/capture-run.json"
download_capture_receipt "$CAPTURE_RECEIPT" "$CAPTURE_RUN"
validate_capture_authorization
jq -e '.evidence' "$CAPTURE_RECEIPT" >"$WORK_DIR/capture-evidence.json"
[[ "$(jq -r .evidence_sha256 "$WORK_DIR/capture-evidence.json")" ==  "$(jq -r .evidence_sha256 "$INITIAL_BUNDLE/validated.json")" ]]
INITIAL_PROGRESS=$(jq -er .progress_index "$INITIAL_BUNDLE/validated.json")
[[ "$INITIAL_PROGRESS" =~ ^[0-5]$ ]]
if ((INITIAL_PROGRESS >= 1)); then update_journal '.registry.tag_lock = "preexisting_verified"'; fi
if ((INITIAL_PROGRESS >= 2)); then update_journal '.registry.manifest_lock = "preexisting_verified"'; fi
if ((INITIAL_PROGRESS >= 3)); then update_journal '.workloads["cosmaneura-staging-worker"] = "preexisting_verified"'; fi
if ((INITIAL_PROGRESS >= 4)); then update_journal '.workloads["cosmaneura-staging-scheduler"] = "preexisting_verified"'; fi
if ((INITIAL_PROGRESS >= 5)); then update_journal '.workloads["cosmaneura-staging-app"] = "preexisting_verified"'; fi
if ((INITIAL_PROGRESS == 5)); then
	validate_capture_authorization
	FINAL_STATUS="complete"
	FINAL_PHASE="already_pinned_revalidated"
	exit 0
fi
if ((INITIAL_PROGRESS < 1)); then
	FINAL_PHASE="tag_lock"
	revalidate_progress 0 tag-lock
	verify_control_plane
	validate_capture_authorization
	update_journal '.registry.tag_lock = "attempted"'
	az acr repository update --name "$REGISTRY_NAME"  --image "$REGISTRY_REPOSITORY:$SOURCE_TAG" --write-enabled false  --delete-enabled false --output none
	az acr repository show-tags --name "$REGISTRY_NAME" --repository "$REGISTRY_REPOSITORY"  --detail --query "[?name=='$SOURCE_TAG']" -o json >"$WORK_DIR/tag-lock.json"
	python3 "$VALIDATOR" validate-lock --policy "$POLICY" --kind tag  --metadata "$WORK_DIR/tag-lock.json"
	update_journal '.registry.tag_lock = "verified"'
fi
if ((INITIAL_PROGRESS < 2)); then
	FINAL_PHASE="manifest_lock"
	revalidate_progress 1 manifest-lock
	verify_control_plane
	validate_capture_authorization
	update_journal '.registry.manifest_lock = "attempted"'
	az acr repository update --name "$REGISTRY_NAME"  --image "$REGISTRY_REPOSITORY@$MANIFEST_DIGEST" --write-enabled false  --delete-enabled false --output none
	az acr manifest show-metadata --registry "$REGISTRY_NAME"  --name "$REGISTRY_REPOSITORY@$MANIFEST_DIGEST" -o json  >"$WORK_DIR/manifest-lock.json"
	python3 "$VALIDATOR" validate-lock --policy "$POLICY" --kind manifest  --metadata "$WORK_DIR/manifest-lock.json"
	update_journal '.registry.manifest_lock = "verified"'
fi
next_progress=3
for workload in cosmaneura-staging-worker cosmaneura-staging-scheduler cosmaneura-staging-app; do
	if ((INITIAL_PROGRESS >= next_progress)); then
		next_progress=$((next_progress + 1))
		continue
	fi
	FINAL_PHASE="revision_copy_$workload"
	from_revision=$(jq -er --arg name "$workload"  '.workloads[] | select(.name == $name) | .original_revision' "$POLICY")
	revalidate_progress "$((next_progress - 1))" "$workload"
	verify_control_plane
	validate_capture_authorization
	update_journal ".workloads[\"$workload\"] = \"attempted\""
	az containerapp revision copy --resource-group "$RESOURCE_GROUP" --name "$workload"  --from-revision "$from_revision"  --image "$REGISTRY_SERVER/$REGISTRY_REPOSITORY@$MANIFEST_DIGEST"  --revision-suffix "$PINNED_SUFFIX" --output none
	wait_for_pinned_workload "$workload" "$INITIAL_BUNDLE/validated.json"  "$WORK_DIR/$workload-pinned.json"
	update_journal ".workloads[\"$workload\"] = \"verified\""
	next_progress=$((next_progress + 1))
done
FINAL_PHASE="final_observation"
FINAL_BUNDLE="$WORK_DIR/final"
collect_bundle "$FINAL_BUNDLE"
FINAL_EVIDENCE="$FINAL_BUNDLE/validated.json"
python3 "$VALIDATOR" validate-transition --policy "$POLICY" --before "$CURRENT_BASELINE" --after "$FINAL_BUNDLE/validated.json"
FINAL_STATUS="complete"
FINAL_PHASE="complete_pending_independent_review"
