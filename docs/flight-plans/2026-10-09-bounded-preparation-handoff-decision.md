# Decision: reuse credentials, authorize each operation afresh

Accepted for the existing personal staging cohort. Issuing a new mount context
for each adjacent preparation step repeats synchronous policy/interaction setup.
Keep one opaque context briefly on the server and consume it per next step.
This is a credential handoff, not an authorization cache. Fresh Core native
identity/policy/visit checks plus existing durable capture authorization remain.
Bound retention to 64 entries, 30 seconds, exact account/JWT/revision/visit/binding
and next step. Drop on failure, mismatch, expiry or restart; never retry uncertain
mutations. Publish no credential to a device. Core receiver deploys before sender.

Receiver-only reuse was rejected because ordinary capture alone omitted the
native route's separate human JWT, agent-workload and encounter-policy boundary.
The chosen additive native actions retain those checks. Consent, session creation,
encryption, leases and source-start implementations are unchanged. The retained
mount context can also avoid mobile setup without a new TestFlight binary.

See ../flight-plans/2026-10-09-bounded-preparation-handoff.md for validation and
release boundaries. If retention does not improve repeated installed startup,
revert the sender handoff and keep the original negotiation path.
