# Scribe inside the three existing Core containers — Tier 2

The existing Scribe reuse Terraform places separate Apps/jobs on the Core
environment and adds paid private endpoints. That does not satisfy strict reuse
of Core's API, worker and scheduler. Prepare default-off Core source hooks for
an independently sealed Scribe runtime layered on the same immutable Core image.
No new Azure app/job, environment, queue, identity, database server, endpoint,
model deployment or dependency is introduced. This source applies no Terraform,
SQL, grants, identity assignment, network change, image publication or activation.
The exact starting Core provider-context source is PR921 head b4e5c56810237ab3dfb33a0954083cc87a0c41f6;
its merge/source disposition must be reconciled before artifact preparation.

`SCRIBE_BUNDLE_API_ENABLED=false` retains the exact existing uvicorn app target
and arguments. Explicit true selects a gateway whose non-Scribe requests go
directly through the unchanged Core app, middleware and lifespan. Only existing
receiver `/provider/v1/` and closed existing Scribe `/v1` resource prefixes cross
a fixed loopback origin. Core service/native/auth routes never cross it. Human,
workload, origin and ACA provenance headers retain separate values; hop headers
cannot remove those authority inputs. Core denials, Scribe unavailability or
child death never fall back to another backend. The child starts/stops with
Core lifespan, binds only 127.0.0.1, and runs as receiver user 10001:10001.
Core image user, command, dependencies and environment stay under Core ownership.

Child environments contain only fixed runtime paths, necessary platform-managed
identity transport values and explicitly selected public Scribe configuration.
Core database, Clerk, client-secret and other credential variables never cross.
Only ManagedIdentityCredential is selected. The Scribe child owns sealed source,
qualified Clerk verifier, closed Core-context receiver configuration, independent
workflow/session/workload boundaries and original capture/worker gates.
The API subprocess startup check is private and is not exposed as a public
Core health route; existing Core health alone does not qualify Scribe operation.

`SCRIBE_BUNDLE_CAPTURED_WORKER_ENABLED=false` adds no scheduler entry or pickups.
Explicit true adds a no-argument task to existing beat/broker/worker. A fixed
content-free lock in the existing Core Redis service prevents concurrent pickup;
durable Scribe SQL leases remain authoritative. Speech then translation each
run the existing pending CLI for one configured tenant, batch one, in separate
nonroot subprocesses with a 180-second deadline. The task expires after 60
seconds in the queue, has no automatic Celery retries, a 370-second soft limit
and 390-second hard limit. Cleanup terminates then kills a stuck child; the
existing child signal cancellation closes clients and preserves fenced retry
state. No patient, tenant, actor, token or recording identifier enters the broker.
Only fixed stage/outcome metadata is logged by Core. Existing tasks/schedules stay.

The exact already-existing metadata identities are distinct: workflow MI on API,
health-services MI for speech, foundation-runtime MI for translation. The latter
is not currently attached to the Core worker. Attaching it and all DB/key/Blob/AI
grants are held owner mutations, not executed prerequisites. The migration MI
is never repurposed. API must remain key Reader only; both workers require
key-scoped unwrap, speech container-scoped Blob Reader, and stage-specific AI
roles. The shared Core worker is a trusted container holding two worker MIs;
these identities do not become separate OS/container security domains.
The metadata audit found the foundation runtime already has account-wide Blob
Data Contributor, AcrPull and key-scoped Reader. Translation must not inherit
that Blob writer privilege: reducing its existing grant requires a separate
owner/Scribe-IaC plan before attachment/activation. Core health-services currently
has only its existing DeID role; required Scribe worker grants are absent.
The new IaC projection targets only the same three Core containers. Its separate
identity-attachment flag defaults false and performs only an exact existing-MI
metadata read before a later owner-approved attachment. It creates no MI/grant.

Existing Core has no VNet. Existing AI public endpoints/model are observed;
the separately reviewed default-off exact-account AI transport is required.
Existing Scribe KV and Blob public access are currently Disabled. The prior
exact Core egress 64.236.176.41/32 KV/public-ciphertext network policy is source,
not applied, and its worker read/unwrap use needs explicit owner disposition.
No private endpoint or new paid resource is proposed. Core PG remains Ready,
password Enabled, Entra Disabled. Its auth-only plan is held at the existing
credential timeout; schema/bootstrap, principal/tenant RLS and migration checks
remain owner-reserved. Registry DDL is not needed for the new Core-context mode.

This is process/transport source, not full native creation/capture parity. The
merged receiver Core-context backend is an honest read-only catalog/context rung.
New patients/visits and inherited service mutations remain unavailable under
their existing separate Core gates; this source never globally enables Agent API,
Authority Kernel, Session Prep, encounter creation, capture, handoff or a cohort.
Keep operational flags off until the intended native scope, actual Clerk/session
receipts, source/schema/image matrix and canary/recovery evidence are reviewed.

Offline checks must cover unchanged Core routes/lifespan, exact token/provenance
forwarding, no denial fallback, child isolation/termination, disabled schedules,
bounded worker subprocesses, and the sealed producer/consumer source seam.
No live Redis, PG, Clerk, Azure or model call is part of these checks. Building
the layered image must independently qualify Core's inspected runtime user,
Python3.13, existing file permissions, child UID and all Core process smokes;
source checks do not claim that an image was built or promoted.

Review final source independently before publication. Before deployment, discard
the paired Core/Scribe commits. After separately approved deployment, stop new
capture and scheduler pickup, drain accepted work, disable both bundle flags and
restore a qualified migration-compatible prior image/configuration pair. Retain
schemas, mappings, identity assignments, keys, recordings and audit data. Network
or grant revocation waits for drain and SAS expiry and requires its own plan.


The coherent existing-patient source rung combines reviewed hooks 173fcf02 with
reviewed native visit facade dfb7fde plus gate correction 46aeea2, on exact Core
provider-context 5fe61be (dependent on its merge). Runtime hook and native service
blobs are preserved; shared IaC keeps both default-off controls and all tests.
The new native visit gate permits only patient search and create(existing),
retaining the existing writer/receipt/audit/current authority checks. Its fresh
commit gate cannot fall back to an enabled browser gate after native revocation.
Scribe's matching consumer and explicit existing-patient-only native UI must be
qualified first. Full Start-to-Save still requires its closed native mutation
consumer; new/provisional patient, matching, DOB and private-note parity stay
open. This combination adds no resource, grant, dependency or production action.
