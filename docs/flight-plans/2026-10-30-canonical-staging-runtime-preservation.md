# Preserve approved runtime configuration in canonical staging deployments

Tier 2: protected staging deployment configuration. Core staging currently has
`SCRIBE_WORKFLOW_ENABLED=true`. `Deploy Staging Candidate` enters the legacy
service-principal deployment branch, whose configuration guard requires that
flag to be false. It therefore cannot advance application source while retaining
the approved Scribe integration.

Add one required `staging_effective_configuration_sha256` dispatch input and bind
the existing full configuration preservation mode in the deployment job. The
configuration guard accepts the already-enabled Scribe workflow only in version
2 or full preservation mode. The complete effective environment and workload
arguments must match the reviewed hash, and the existing checks compare every
current/ready workload against the desired configuration before migrations and
again immediately before each update. Preservation mode skips secret writes and
unused-secret deletion. Missing hashes, rotations, unexpected configuration,
unready revisions, and workload drift continue to refuse the release.

Record the requested, expected, and observed full configuration fingerprint in
the existing request, release, and terminal attempt evidence. Preserve founder
dispatch, protected-current-master control source, exact merged candidate,
schema, recovery digest, staging identity, credentialless build, and immutable
publication gates. There is no founder-user identity fallback, new provider,
environment setting change, schema change, production action, or deployment
authorization from this source change.

The candidate remains the exact merge of selected PR903,
`5ae2ca2b385d825c8b376dbb24be06c50ebd5219`. The later control-fix merge is the
workflow source; it does not implicitly replace the selected candidate. Assimilate
the already-approved application work contained in the candidate while retaining
the current staging configuration and records. Core schema before/after is
`3d40e4e90e05`, freshly observed before dispatch and unchanged in candidate source.
The current recovery workloads share digest
`sha256:cbdfcbe170431a0c7edcc09c26985ebcc751519470ed61a70f2b2416a9269b5e`.

The synthetic confirmation covers migration and smoke evidence as defined by
the workflow. Existing isolated fixture restore/no-op evidence is retained only
where its migration inputs are byte-identical. It does not assert that the entire
persistent staging database is PHI-free. Identity and full runtime configuration
manifests still require independent review before dispatch.

Focused validation passed: 201 tests across the existing canonical staging,
runtime preservation, candidate verifier, and release contract suites. Six
preserved-enabled Scribe cases fail at the real configuration guard on the exact
PR903 baseline and pass with this change; five cases retain the legacy and V2
boundaries. The terminal gate accepts the requested preserved hash and rejects
missing, mismatched, or unattested configuration. Ruff, Bash syntax, YAML syntax
and duplicate-key validation, and whitespace checks passed. These checks use
synthetic inputs with application conftest and provider hooks disabled.
Independent final review and repository-required PR checks remain required.

Before deployment, recovery is to discard the proposed control change or revert
it through review; staging remains unchanged. After deployment, use the bound
immutable recovery image with retained configuration and schema. Recovery is a
separately selected operation and never removes records or additive schema.
