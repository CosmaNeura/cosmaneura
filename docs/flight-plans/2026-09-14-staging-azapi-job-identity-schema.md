# Staging AzAPI compatibility for a pull-only Job identity

Tier 2. Source-only prerequisite; no runtime resource or execution is added.
Base: merged PR389, `5535c2430587afc169b41974673e8cb8b7987fe9`.

## Narrow change

Pin Azure/azapi2.4.0 only in infra-staging-rehearsal and update its exact lock.
Keep AzureRM4.73.0 and OpenTofu1.12.5 unchanged, along with every resource,
activation flag, identity/grant, backend, operator packet and live state.
The other infrastructure roots retain their existing provider versions.

The existing plan validator's strict AzAPI version pin moves from2.1.0 to2.4.0
at the same time; no range or compatibility fallback is added. Its two test
fixtures and exact dependent bootstrap/launcher SHA256 pins are updated in
lockstep. The bootstrap/consumer changes are digest substitutions only, with
no timing, identity, filesystem, claim, approval or execution-logic changes.
Old-provider plans remain rejected by the new source. The existing pinned
operator checkout and historical artifacts are not edited or executed.

AzAPI2.1.0 embeds Microsoft.App schemas through2024-10-02-preview and lacks
stable2025-01-01. Public v2.2.0/v2.3.0 also lack the2025stable schema; v2.4.0
contains it. Jobs2025-01-01 already supports configuration.identitySettings,
including lifecycle=None, so2025-07-01 is not needed for this preparatory rung.
The v2.4.0 release also adds local resource schema validation during validate.
This is a bounded compatibility pin, not a claim that2.4.0 is latest or that
its age/security posture has received live runtime acceptance.

## Non-executing schema regression

The standalone synthetic fixture is nested outside the root module. It uses
nonexistent synthetic IDs and an example.invalid image, has no backend, disables
CLI/MSI/OIDC authentication and Azure preflight, and retains schema validation.
The checker copies it and the root lock into an isolated temporary directory,
forwards no credential/proxy/CLI-configuration environment, initializes from
this root's already installed provider packages with backend=false and
lockfile=readonly, and runs local validate only. Package hashes remain checked;
the checker accepts no caller/environment-selected mirror and does not repeat
network downloads. The preceding normal root init supplies the signed packages.
The upstream lifecycle schema includes a string fallback; do not rely on it
rejecting arbitrary future strings. A
boolean lifecycle must fail specifically at that type-checked field; None must
pass. This does not claim the provider rejects other lifecycle strings such as
All, or establishes the desired effective Azure identity policy. The fixture
declares both root providers to preserve the exact full lockfile; its unused
AzureRM provider also disables identity methods and provider registration.
The checker never invokes plan, apply, refresh, state, import, Job start or Azure.
Its output permanently denies live Job support, runtime acceptance and activation.

The real-root provider-mocked tests must also pass because this dependency
change affects their provider validation. Rechecking affected dependency tests
does not reuse their former passing result. Unrelated application suites need
not be rerun locally; normal PR CI remains separate.

## Provider behavior and future human plan

Intervening releases change output handling, retry behavior and schema checks.
The2.2.0 notes explicitly warn of output-only state differences. Do not run
refresh, mutate state, normalize a plan or repair existing resources here.
Before any live use, create a NEW exact independently reviewed human plan with
the final merged provider/lock/source pins and inspect all resulting differences.
Never reuse the consumed foundation plan/receipt/claim or merely upgrade its
working directory. No production or staging apply is part of this PR.

## Limits and recovery

Schema acceptance is not effective Azure identity isolation. The exact Job,
pull identity/grant, private publication path, image readback/provenance, network,
capture, cleanup, fresh plan/handoff and actual human execution remain separate
unresolved/reviewed gates. No image is uploaded or run. No public ACR access,
preview service, provider schema bypass or workflow dispatch is introduced.
The workflow changes only secretless PR validation, not deployment paths.
Its path filter explicitly includes checker-only changes. The staging root's
preceding init uses lockfile=readonly and checks the tracked lock is unchanged
before the schema check reads it. These close the two P2 findings from the
first exact-source review; final corrected-source review remains required.
Two new workflow regressions and readonly root initialization pass; the tracked
lock remains byte-identical. The original21harness cases were not replayed.

Recovery before any live use: normally revert the provider pin/lock, standalone
fixture/check and CI step. Once live resources have been planned or applied with
a different provider, review compatibility before any downgrade; do not assume
provider rollback repairs state. No migration or feature-flag rung applies.

Exact final-source independent review and protected CI are required. This PR
has no merge or deployment approval merely because preparation was authorized.

## Local verification recorded September14

- Credential-neutral, backend-disabled initialization installed signed locked
  AzAPI2.4.0 and unchanged AzureRM4.73.0. The lock diff changes only AzAPI.
- Real provider local schema check passes: a boolean lifecycle is rejected at
  properties.configuration.identitySettings.0.lifecycle, while None is accepted.
  Hash-verified local package reuse avoids repeating provider downloads.
- Root validate and all78provider-mocked infrastructure tests pass under the
  changed dependency. No live state, backend, provider registration or Azure
  preflight was used.
-21new schema-harness unit cases pass. Seven new exact-provider-pin cases and
  four affected plan-safety/state-refresh cases pass; bootstrap hashes match.
- Ruff0.11.6 and Pyright1.1.399 pass for the new harness/tests. Source-specific
  lint and format checks pass. Unrelated passing suites were not replayed.

The first local provider invocation lacked sandbox IPC permission; its approved
credential-neutral retry passed. Initial harness attempts exposed a mismatched
fixture dependency set and a download failure; the final checker preserves the
full root lock and reuses its installed packages. The negative-control assertion
matches the exact field-specific schema error rather than assuming diagnostic
text contains a boolean type name. These failures are not Azure observations.

Public evidence:

- https://github.com/Azure/terraform-provider-azapi/releases/tag/v2.4.0
- https://github.com/Azure/terraform-provider-azapi/releases/tag/v2.2.0
- https://github.com/Azure/terraform-provider-azapi/tree/v2.4.0/internal/azure/generated/app/microsoft.app/2025-01-01
- https://github.com/Azure/azure-rest-api-specs/blob/main/specification/app/resource-manager/Microsoft.App/ContainerApps/stable/2025-01-01/Jobs.json
- https://learn.microsoft.com/en-us/azure/container-apps/managed-identity
