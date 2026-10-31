# Automatic existing-provider Session Prep access

Tier 2: provider/account authorization and owned patient-record scope. One
source outcome; this preparation changes no live account, grant, billing,
schema, rollout flag, service, image or clinical note.

Current completed-onboarding providers still require active current practice
membership, roster approval or workforce attestation, and an in-force BAA.
After those checks, the practice can be entitled normally or by an existing
active pilot grant whose validated Doctor/email/time evidence and practice
snapshot match. This does not adopt the broader dashboard pilot bypass or
create/renew grants. New providers retain the same ordinary entitlement path.

An ordinary old visit can acquire its missing ProviderRecordAuthority during
verified exchange. The implementation locks its exact existing binding,
rechecks current owned appointment/patient/practice, active actor/tenant/provider
membership and exact provider-entry origin, and preserves those identities.
Fixture rows, partial fixture audit evidence, imported/mixed origins, actor-null
or revoked/ambiguous authority cannot use this path. Only the missing record
scope and its ordinary binding audit are appended, followed by full existing
lineage validation. Reads do not reconcile; the existing exchange savepoint,
unique binding/appointment constraints and Practice/Doctor/Patient fences remain.

Qualification uses the existing real domain fixtures and normal PostgreSQL
conftest: current same-practice pilot versus missing/unbound/foreign/future/
changed-identity evidence; unchanged membership/BAA/onboarding/revocation
denials; ordinary legacy exchange/repeat and actual binding-lock SQL; fixture,
partial/imported/foreign/revoked/practice-change rejection. Source-only syntax
or mock checks do not substitute for these database cases or live isolation.

Release remains receiver-first on a reviewed merged candidate with the existing
schema and exact Core/provider/frontend authority contract. No source prep
activates production or makes a refused/generation-only result accepted.

Recovery reverts this application behavior or disables the existing global
availability switch through a separately reviewed release. Retain appended
record scopes/audits and existing grants; do not delete mappings, revive
tombstones, change billing or remove schema as rollback. Existing bindings are
still subject to current ownership and practice checks on every request.
