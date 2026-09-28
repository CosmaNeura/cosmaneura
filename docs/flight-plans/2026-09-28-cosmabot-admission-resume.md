# CosmaBot admission compatibility

Tier 2: first-party capture session contract. No schema, credential, permission,
consent policy, or patient-record change.

The deployed CosmaBot client sends `resume_current` when opening a saved
recording and `expected_session_id` before an operation. Core rejected both
fields with HTTP 422. Its configured admission response also omitted the saved
language selection required by the client.

Accept these mutually exclusive resume modes without new consent or preference
inputs. Resolve the existing authorized binding, reject a mismatched session
before downstream work, and preserve the stored admission family. An absent
admission still requires explicit consent. Return the saved configured selection
as structured data. Existing requests and ordinary admission response shape
remain compatible.

Run local receiver and real disposable PostgreSQL admission/recovery checks,
then verify the actual CosmaBot serializer against the Core receiver. Record the
tested commit and implementer review in the PR. Merge before staging rollout.
The separate CosmaBot maintenance window does not itself authorize a Core
staging rollout or an environment flag change.

Roll out the reviewed Core image while preserving the current staging images
for recovery, existing configuration, schema, identities, and neighboring
services. Separately restore `SCRIBE_ENCOUNTER_PREP_ENABLED=true` only under
owner-approved Core staging maintenance. Recheck the synthetic Mac flow.
Rollback restores the prior Core image and exact prior flag; no data rollback.
