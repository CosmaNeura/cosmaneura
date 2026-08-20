#!/usr/bin/env bash
set -euo pipefail

# Dry-run/read-only by default. This command intentionally knows only the
# provider staging UAI; the public UAI is excluded from the provider-first V7
# rollout. It never prints access tokens or assignment bearer material.
APP_REGISTRATION_OBJECT_ID="352d67fb-1d11-42bd-acee-008d720cae32"
RESOURCE_SERVICE_PRINCIPAL_ID="a7035858-8f8e-4f5c-a484-3efac710d2ce"
AGENT_API_CLIENT_ID="af957741-0342-498b-8496-e93706af9e99"
EXPECTED_TENANT_ID="d51754d0-bba5-4810-820e-afac542844d6"
EXPECTED_SUBSCRIPTION_ID="bab2a709-1fcb-4c87-a9fc-802f7477ec13"
PROVIDER_UAI_CLIENT_ID="8cb2ecb5-3554-4405-be6f-5eda985c2f04"
PROVIDER_UAI_PRINCIPAL_ID="96d232e3-2b2e-416b-91fb-066565efac93"
PUBLIC_UAI_CLIENT_ID="5074cac1-5555-43e9-a469-3e0b7e16608e"
PUBLIC_UAI_PRINCIPAL_ID="b35e8945-c8ee-4e5b-af55-9e1176d7acaf"
SCRIBE_ROLE_ID="90151a73-578b-4a7d-aa9c-58372dfc2646"
SCRIBE_ROLE_VALUE="Scribe.Transcript.Write"
AGENT_ROLE_ID="5f7c87d1-b2c1-4e92-9d51-54c3160fdfe6"
AGENT_ROLE_VALUE="AgentApi.Access"

mode="status"
confirm_staging="false"
for argument in "$@"; do
  case "$argument" in
    --apply) mode="apply" ;;
    --revoke) mode="revoke" ;;
    --confirm-staging) confirm_staging="true" ;;
    --status) mode="status" ;;
    *)
      echo "Usage: $0 [--status | --apply | --revoke] [--confirm-staging]" >&2
      exit 2
      ;;
  esac
done

if [[ "$mode" != "status" && "$confirm_staging" != "true" ]]; then
  echo "Refused: --apply/--revoke requires --confirm-staging." >&2
  exit 2
fi
command -v az >/dev/null
command -v jq >/dev/null

account_json="$(az account show -o json)"
[[ "$(jq -r '.tenantId' <<<"$account_json")" == "$EXPECTED_TENANT_ID" ]] || {
  echo "Refused: Azure CLI is signed into the wrong Entra tenant." >&2
  exit 1
}
[[ "$(jq -r '.id' <<<"$account_json")" == "$EXPECTED_SUBSCRIPTION_ID" ]] || {
  echo "Refused: Azure CLI is targeting the wrong subscription." >&2
  exit 1
}

app_json="$(
  az rest \
    --method GET \
    --uri "https://graph.microsoft.com/v1.0/applications/$APP_REGISTRATION_OBJECT_ID" \
    -o json
)"
resource_sp_json="$(az ad sp show --id "$RESOURCE_SERVICE_PRINCIPAL_ID" -o json)"
provider_sp_json="$(az ad sp show --id "$PROVIDER_UAI_PRINCIPAL_ID" -o json)"
public_sp_json="$(az ad sp show --id "$PUBLIC_UAI_PRINCIPAL_ID" -o json)"

[[ "$(jq -r '.id' <<<"$app_json")" == "$APP_REGISTRATION_OBJECT_ID" ]] || {
  echo "Refused: unexpected application registration object." >&2
  exit 1
}
[[ "$(jq -r '.appId' <<<"$app_json")" == "$AGENT_API_CLIENT_ID" ]] || {
  echo "Refused: Agent API application audience mismatch." >&2
  exit 1
}
[[ "$(jq -r '.id' <<<"$resource_sp_json")" == "$RESOURCE_SERVICE_PRINCIPAL_ID" ]] || {
  echo "Refused: unexpected Agent API resource service principal." >&2
  exit 1
}
[[ "$(jq -r '.appId' <<<"$resource_sp_json")" == "$AGENT_API_CLIENT_ID" ]] || {
  echo "Refused: Agent API resource audience mismatch." >&2
  exit 1
}
[[ "$(jq -r '.id' <<<"$provider_sp_json")" == "$PROVIDER_UAI_PRINCIPAL_ID" ]] || {
  echo "Refused: unexpected provider UAI principal." >&2
  exit 1
}
[[ "$(jq -r '.appId' <<<"$provider_sp_json")" == "$PROVIDER_UAI_CLIENT_ID" ]] || {
  echo "Refused: provider UAI client/principal binding mismatch." >&2
  exit 1
}
[[ "$(jq -r '.id' <<<"$public_sp_json")" == "$PUBLIC_UAI_PRINCIPAL_ID" ]] || {
  echo "Refused: unexpected public UAI principal." >&2
  exit 1
}
[[ "$(jq -r '.appId' <<<"$public_sp_json")" == "$PUBLIC_UAI_CLIENT_ID" ]] || {
  echo "Refused: public UAI client/principal binding mismatch." >&2
  exit 1
}

provider_assignments_before="$(
  az rest \
    --method GET \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$PROVIDER_UAI_PRINCIPAL_ID/appRoleAssignments" \
    -o json
)"
public_assignments_before="$(
  az rest \
    --method GET \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$PUBLIC_UAI_PRINCIPAL_ID/appRoleAssignments" \
    -o json
)"
public_agent_assignment_count="$(
  jq --arg resource "$RESOURCE_SERVICE_PRINCIPAL_ID" --arg role "$AGENT_ROLE_ID" '
    [.value[] | select(.resourceId == $resource and .appRoleId == $role)] | length
  ' <<<"$public_assignments_before"
)"
[[ "$public_agent_assignment_count" -eq 0 ]] || {
  echo "Refused: public UAI has an AgentApi.Access assignment." >&2
  exit 1
}
provider_other_assignments_before="$(
  jq -cS --arg resource "$RESOURCE_SERVICE_PRINCIPAL_ID" --arg role "$AGENT_ROLE_ID" '
    [.value[] | select(.resourceId != $resource or .appRoleId != $role)]
    | sort_by(.id)
  ' <<<"$provider_assignments_before"
)"
public_assignments_canonical_before="$(
  jq -cS '[.value[]] | sort_by(.id)' <<<"$public_assignments_before"
)"

scribe_role="$(
  jq -c --arg id "$SCRIBE_ROLE_ID" --arg value "$SCRIBE_ROLE_VALUE" '
    [.appRoles[] | select(.id == $id and .value == $value)]
    | if length == 1 then .[0] else empty end
  ' <<<"$app_json"
)"
[[ -n "$scribe_role" ]] || {
  echo "Refused: the existing Scribe.Transcript.Write role is absent or ambiguous." >&2
  exit 1
}
[[ "$(jq -r '.isEnabled and (.allowedMemberTypes == ["Application"])' <<<"$scribe_role")" == "true" ]] || {
  echo "Refused: the existing Scribe.Transcript.Write role shape changed." >&2
  exit 1
}
scribe_before="$(jq -cS . <<<"$scribe_role")"

agent_roles="$(
  jq -c --arg id "$AGENT_ROLE_ID" --arg value "$AGENT_ROLE_VALUE" '
    [.appRoles[] | select(.id == $id or .value == $value)]
  ' <<<"$app_json"
)"
agent_role_count="$(jq 'length' <<<"$agent_roles")"
if [[ "$agent_role_count" -gt 1 ]]; then
  echo "Refused: AgentApi.Access role identity is ambiguous." >&2
  exit 1
fi
if [[ "$agent_role_count" -eq 1 ]]; then
  [[ "$(jq -r --arg id "$AGENT_ROLE_ID" --arg value "$AGENT_ROLE_VALUE" '
    .[0].id == $id and .[0].value == $value and .[0].isEnabled
    and (.[0].allowedMemberTypes == ["Application"])
  ' <<<"$agent_roles")" == "true" ]] || {
    echo "Refused: AgentApi.Access exists with a conflicting definition." >&2
    exit 1
  }
fi
roles_before="$(
  jq -cS '[.appRoles[] | {
    allowedMemberTypes,description,displayName,id,isEnabled,value
  }] | sort_by(.id)' <<<"$app_json"
)"
expected_roles_after="$roles_before"
if [[ "$mode" == "apply" && "$agent_role_count" -eq 0 ]]; then
  expected_roles_after="$(
    jq -cS --arg id "$AGENT_ROLE_ID" --arg value "$AGENT_ROLE_VALUE" '
      . + [{
        allowedMemberTypes: ["Application"],
        description: "Allows the provider Sovereign workload to call the governed Agent API.",
        displayName: "Agent API access",
        id: $id,
        isEnabled: true,
        value: $value
      }] | sort_by(.id)
    ' <<<"$roles_before"
  )"
fi

if [[ "$mode" == "apply" && "$agent_role_count" -eq 0 ]]; then
  app_etag="$(jq -r '."@odata.etag" // empty' <<<"$app_json")"
  [[ -n "$app_etag" ]] || {
    echo "Refused: Agent API application ETag is unavailable." >&2
    exit 1
  }
  umask 077
  work_directory="$(mktemp -d)"
  trap 'rm -rf "$work_directory"' EXIT
  jq --arg id "$AGENT_ROLE_ID" --arg value "$AGENT_ROLE_VALUE" '
    {
      appRoles: (
        .appRoles + [{
          allowedMemberTypes: ["Application"],
          description: "Allows the provider Sovereign workload to call the governed Agent API.",
          displayName: "Agent API access",
          id: $id,
          isEnabled: true,
          value: $value
        }]
      )
    }
  ' <<<"$app_json" >"$work_directory/app-role-patch.json"
  az rest \
    --method PATCH \
    --uri "https://graph.microsoft.com/v1.0/applications/$APP_REGISTRATION_OBJECT_ID" \
    --headers "Content-Type=application/json" "If-Match=$app_etag" \
    --body "@$work_directory/app-role-patch.json" \
    --output none
  app_json="$(
    az rest \
      --method GET \
      --uri "https://graph.microsoft.com/v1.0/applications/$APP_REGISTRATION_OBJECT_ID" \
      -o json
  )"
fi

roles_after="$(
  jq -cS '[.appRoles[] | {
    allowedMemberTypes,description,displayName,id,isEnabled,value
  }] | sort_by(.id)' <<<"$app_json"
)"
[[ "$roles_after" == "$expected_roles_after" ]] || {
  echo "Refused: application roles changed outside the exact reconciliation." >&2
  exit 1
}

scribe_after="$(
  jq -cS --arg id "$SCRIBE_ROLE_ID" --arg value "$SCRIBE_ROLE_VALUE" '
    [.appRoles[] | select(.id == $id and .value == $value)]
    | if length == 1 then .[0] else empty end
  ' <<<"$app_json"
)"
[[ "$scribe_after" == "$scribe_before" ]] || {
  echo "Refused: Scribe.Transcript.Write changed during reconciliation." >&2
  exit 1
}

if [[ "$mode" == "apply" ]]; then
  agent_app_role_ok="$(
    jq -r --arg id "$AGENT_ROLE_ID" --arg value "$AGENT_ROLE_VALUE" '
      [.appRoles[] | select(
        .id == $id and .value == $value and .isEnabled
        and (.allowedMemberTypes == ["Application"])
      )] | length == 1
    ' <<<"$app_json"
  )"
  [[ "$agent_app_role_ok" == "true" ]] || {
    echo "Refused: AgentApi.Access application role did not verify." >&2
    exit 1
  }

  # Graph may briefly lag when projecting a new app role onto the resource SP.
  resource_role_ok="false"
  for _ in 1 2 3 4 5 6; do
    resource_sp_json="$(az ad sp show --id "$RESOURCE_SERVICE_PRINCIPAL_ID" -o json)"
    resource_role_ok="$(
      jq -r --arg id "$AGENT_ROLE_ID" --arg value "$AGENT_ROLE_VALUE" '
        [.appRoles[] | select(
          .id == $id and .value == $value and .isEnabled
          and (.allowedMemberTypes == ["Application"])
        )] | length == 1
      ' <<<"$resource_sp_json"
    )"
    [[ "$resource_role_ok" == "true" ]] && break
    sleep 2
  done
  [[ "$resource_role_ok" == "true" ]] || {
    echo "Refused: AgentApi.Access was not projected to the resource service principal." >&2
    exit 1
  }
fi

assignments_json="$provider_assignments_before"
assignment_ids="$(
  jq -r --arg resource "$RESOURCE_SERVICE_PRINCIPAL_ID" --arg role "$AGENT_ROLE_ID" '
    [.value[] | select(.resourceId == $resource and .appRoleId == $role)] | .[].id
  ' <<<"$assignments_json"
)"
assignment_count="$(sed '/^$/d' <<<"$assignment_ids" | wc -l | tr -d ' ')"
[[ "$assignment_count" -le 1 ]] || {
  echo "Refused: duplicate AgentApi.Access assignments exist." >&2
  exit 1
}

if [[ "$mode" == "apply" && "$assignment_count" -eq 0 ]]; then
  assignment_body="$(
    jq -cn \
      --arg principal "$PROVIDER_UAI_PRINCIPAL_ID" \
      --arg resource "$RESOURCE_SERVICE_PRINCIPAL_ID" \
      --arg role "$AGENT_ROLE_ID" \
      '{principalId:$principal,resourceId:$resource,appRoleId:$role}'
  )"
  az rest \
    --method POST \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$PROVIDER_UAI_PRINCIPAL_ID/appRoleAssignments" \
    --headers Content-Type=application/json \
    --body "$assignment_body" \
    --output none
elif [[ "$mode" == "revoke" && "$assignment_count" -eq 1 ]]; then
  az rest \
    --method DELETE \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$PROVIDER_UAI_PRINCIPAL_ID/appRoleAssignments/$assignment_ids" \
    --output none
fi

assignments_json="$(
  az rest \
    --method GET \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$PROVIDER_UAI_PRINCIPAL_ID/appRoleAssignments" \
    -o json
)"
assignment_count="$(
  jq --arg resource "$RESOURCE_SERVICE_PRINCIPAL_ID" --arg role "$AGENT_ROLE_ID" '
    [.value[] | select(.resourceId == $resource and .appRoleId == $role)] | length
  ' <<<"$assignments_json"
)"
provider_other_assignments_after="$(
  jq -cS --arg resource "$RESOURCE_SERVICE_PRINCIPAL_ID" --arg role "$AGENT_ROLE_ID" '
    [.value[] | select(.resourceId != $resource or .appRoleId != $role)]
    | sort_by(.id)
  ' <<<"$assignments_json"
)"
[[ "$provider_other_assignments_after" == "$provider_other_assignments_before" ]] || {
  echo "Refused: an unrelated provider app-role assignment changed." >&2
  exit 1
}
public_assignments_after="$(
  az rest \
    --method GET \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$PUBLIC_UAI_PRINCIPAL_ID/appRoleAssignments" \
    -o json
)"
public_assignments_canonical_after="$(
  jq -cS '[.value[]] | sort_by(.id)' <<<"$public_assignments_after"
)"
[[ "$public_assignments_canonical_after" == "$public_assignments_canonical_before" ]] || {
  echo "Refused: a public UAI app-role assignment changed." >&2
  exit 1
}
public_agent_assignment_count="$(
  jq --arg resource "$RESOURCE_SERVICE_PRINCIPAL_ID" --arg role "$AGENT_ROLE_ID" '
    [.value[] | select(.resourceId == $resource and .appRoleId == $role)] | length
  ' <<<"$public_assignments_after"
)"
[[ "$public_agent_assignment_count" -eq 0 ]] || {
  echo "Refused: public UAI exclusion did not verify." >&2
  exit 1
}

if [[ "$mode" == "apply" && "$assignment_count" -ne 1 ]]; then
  echo "Refused: provider staging AgentApi.Access assignment did not verify." >&2
  exit 1
fi
if [[ "$mode" == "revoke" && "$assignment_count" -ne 0 ]]; then
  echo "Refused: provider staging AgentApi.Access revocation did not verify." >&2
  exit 1
fi

agent_role_state="missing"
jq -e --arg id "$AGENT_ROLE_ID" --arg value "$AGENT_ROLE_VALUE" '
  any(.appRoles[]; .id == $id and .value == $value and .isEnabled)
' >/dev/null <<<"$app_json" && agent_role_state="ready"
assignment_state="missing"
[[ "$assignment_count" -eq 1 ]] && assignment_state="ready"

echo "mode=$mode"
echo "scribe_role=preserved"
echo "agent_role=$agent_role_state"
echo "provider_staging_assignment=$assignment_state"
echo "public_assignment=excluded"
