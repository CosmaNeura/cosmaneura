# Staging sanitizer bootstrap job attachment contract

This Tier 2 slice defines the exact dormant boundary for the future single-use
sanitizer bootstrap job. It performs no Azure, provider, credential, database,
workflow, deployment, or mutation operation and does not recreate the spent
bootstrap identity.

The selected job is a manual, per-rehearsal Container Apps job named
`cosmaneura-stg-restore-bootstrap` in the rehearsal control resource group. It
must run in the same future private environment as the provision and cleanup
jobs, use one digest-pinned private image, have no ingress, accept no start
override, run at most once with one replica and no retry, and be deleted after
the lifecycle. Its only compute identity is the separately authenticated
`cosmaneura-staging-restore-bootstrap-job` UAMI. Its sole Azure RBAC assignment
is the built-in `AcrPull` role scoped to the authenticated private registry.
System identity, federation, any other RBAC, additional identities, secrets,
passwords, connection strings, ambient credentials, volumes, public database
access, and VNet peering remain forbidden.

The future control-plane adapter must hold external stability authority before
job creation through final job and database-authority absence readback. It must
create, observe, authenticate, start, poll, stop, delete, and observe in the
reviewed order; it may issue at most one start request and may not retry an
ambiguous outcome. The database session must close before temporary PostgreSQL
administrator removal, administrator absence must be proven before job
deletion, and job/identity-attachment absence must be proven before identity
deletion. Success, failure, and uncertainty all require teardown.

The contract pins the existing bootstrap-binding and restore-runtime-binding
policies. Every authority and implementation claim remains false. Public
activation stays terminal at
`activation.sanitizer_bootstrap_job_attachment_unavailable`. Later reviewed
slices must implement the private executor environment, publish and
authenticate the exact image, create a fresh bounded identity and job, observe
and authenticate their live attachment, and implement the one-shot control
plane. Recovery for this slice is a code-only revert.

Contract SHA-256:
`sha256:7d85f301ac94f04fc6aa4c78b845f3ca3875ea8ac54bb4b1e7c105548a2b9207`.
