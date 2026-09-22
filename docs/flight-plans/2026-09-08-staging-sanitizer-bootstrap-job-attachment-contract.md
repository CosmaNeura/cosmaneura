# Staging sanitizer bootstrap job attachment contract

This Tier 2 slice defines the exact dormant boundary for the future single-use
sanitizer bootstrap job. It performs no Azure, provider, credential, database,
workflow, deployment, or mutation operation and does not recreate the spent
bootstrap identity.

The selected job is a manual, per-rehearsal Container Apps job named
`cosmaneura-stg-restore-bootstrap` in the rehearsal control resource group. It
must run in the same future private environment as the provision and cleanup
jobs, use one digest-pinned private image, have no ingress, accept no start
override, run at most once with one replica and no retry, contain exactly one
application container with that authenticated image and no init container or
sidecar, and be deleted after the lifecycle. Its only compute identity is the
separately authenticated `cosmaneura-staging-restore-bootstrap-job` UAMI. Its
sole Azure RBAC assignment
is the built-in `AcrPull` role scoped to the authenticated private registry.
The image registry resource, host, repository, job registry entry, and pull
identity must all resolve to that same authenticated registry and UAMI. The
registry must be private, credentialless, and reachable only through its exact
private endpoint and DNS binding.
System identity, federation, any other RBAC, additional identities, secrets,
passwords, connection strings, ambient credentials, volumes, public database
access, and VNet peering remain forbidden.

The future control-plane adapter must hold external stability authority across
the complete ordered lifecycle. It first proves the fixed-name UAMI, job, and
applicable `AcrPull` assignment absent, then uses create-only semantics. It
authenticates the new UAMI with zero effective authority before creating and
reading back the exact `AcrPull` assignment. Only then may it observe the
private network, create the job, prove empty pre-start execution history, and
issue one unmodified start. After terminal execution and replica observation it
must reauthenticate the job configuration, image, identity, registry entry,
and pull attachment before database-session close, administrator removal, job
deletion, `AcrPull` deletion, UAMI deletion, and every final absence readback.
It may issue at most one start request and may not retry an ambiguous outcome.
Success requires exactly one provider-observed execution with status
`Succeeded`, one successful replica, and zero failed replicas. Success,
failure, timeout, stop, and uncertainty all require the same teardown.

Pre-start evidence explicitly covers the private Container Apps environment,
subnet route table and NSG, private registry endpoint and DNS, target database
endpoint and DNS, exact job/identity/registry attachment, exhaustive direct and
inherited RBAC inventory, and execution history. The offline validator rejects
non-exact nested Python types, aliases, cycles, floats, excessive depth or node
counts, oversized scalars, and oversized on-disk policy bytes before
serialization or unbounded file reads.

The contract pins the existing bootstrap-binding and restore-runtime-binding
policies. Every authority and implementation claim remains false. Public
activation stays terminal at
`activation.sanitizer_bootstrap_job_attachment_unavailable`. Later reviewed
slices must implement the private executor environment, publish and
authenticate the exact image, create a fresh bounded identity and job, observe
and authenticate their live attachment, and implement the one-shot control
plane. Recovery for this slice is a code-only revert.

Contract SHA-256:
`sha256:9ee7cae5d675ea8727e09254c1d0a5396c043994a54c39d4b50290a168f088d2`.
