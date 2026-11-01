# Session Prep production frontend admission

Tier 2: production build control. One outcome: an explicitly selected, reviewed
manual Core release can compile the existing provider Session Prep panel. The
default remains dark. No account list, schema, runtime, IAM, authentication or
patient-record authority changes.

The active `Build & Publish (manual)` workflow adds a default-false
`production_session_prep_frontend_enabled` input. When selected, the founder
must also supply the exact full reviewed Core SHA in
`production_session_prep_reviewed_core_sha`, equal to that run's current master
source. Retired workflows remain untouched.

The credential-bearing resolver first projects the existing ten public inputs
with the flag forced false. A standalone helper admits true only for that
founder/master/manual workflow context, reviewed source and fixed production
Core and provider origins. It requires a live Clerk public key and rejects
staging/public origins and unresolved 1Password references. A bounded public
receipt binds the resulting configuration digest to the source and workflow
run. The existing credential-free builder rechecks that receipt against its
actual checkout and inputs before emitting the ten NUL-delimited build values.
Both run-local files are removed on success and failure.

The receipt is build evidence, not independently verified provenance, agent
review or an activation approval. The protected workflow establishes founder
authority; caller-created local receipts establish none. Existing founder,
current-master, source provenance, Terraform Apply, provider callback,
credential barrier, publication and deployment gates continue to apply. The
new helper and its existing Clerk decoder are included in the Terraform Apply
source-drift gate.

Before a production activation decision, complete the integrated staging
acceptance and build an independently verified production artifact pair. Fresh
provider foundation/runtime, Core connection and IAM checks, verified TLS,
schema and aggregate provider eligibility preflight remain required. Session
Prep availability still requires fresh server eligibility and own-visit
authorization. The frontend flag grants no chart access and activates no
backend service. Existing/new eligible providers receive the same global
availability automatically; no separate enrolment step is introduced.

Validation exercises the real build-argument recipe: dark defaults, admitted
true, changed actors/ref/event/run/source, staging/public origins, noncanonical
booleans, tampered/missing/linked/duplicate/oversized receipts, and secret
exclusion. These secretless tests create no production readiness evidence.

Recovery: select the default false frontend input for a reviewed recovery
build, or use the verified prior production image and configuration pair under
the existing founder recovery decision. Frontend hiding is not a rollback of
records, schema, IAM or infrastructure. Leave backend gates dark until their
separate activation has been reviewed and authorized.
