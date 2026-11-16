# Native existing-patient visit facade — Tier 2

This independent leaf depends on Core provider-context source 5fe61be and adds
one default-off route: `/service/v1/agent/scribe/native-encounters`. It admits
only the existing `patients` reader and `create` with an `ExistingPatient`.
Fresh Clerk human authentication and the existing singleton native workload
boundary both precede it. Actor/tenant hints must equal the current server-owned
provider context. Cookies, support viewing, alternate interaction headers,
unknown actions, new/provisional patients and mismatched creation keys deny.

The existing `scribe_encounters.encounter_workflow` performs the operation.
Its doctor-owned patient selection, current provider/practice/member checks,
doctor plus creation-key durable receipt, metadata audit, final authorization
and commit-time gate remain intact. A changed nonce retries the original
intent; changed intent under the same key conflicts. There is no new writer,
mapping, schema, consent regime, billing grant or external dependency.

`SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED=false` is the default. Selecting
it requires the dedicated native workload and provider-context flags. Only the
native capture facade receives an effective enabled encounter setting; the
browser encounter, global Authority Kernel/Agent API and Session Prep settings
retain their original values. All existing capture, consent, source, transcript,
notes, retention and workflow-service gates still apply. This flag permits
visit entry; it does not itself authorize recording or clinical processing.

The paired receiver consumer must use the fixed new path, obtain fresh Core
context on both sides of each operation, pass separate human/workload tokens,
retain the exact caller creation key and deny without retry/fallback. Its
catalog must advertise the actual closed subset. The native display currently
accepts only a Staging account; Production display and existing-patient-only
setup require their own small consumer leaf before operational use.

Existing-patient new-visit entry is the first rung. Provisional/new-patient
creation, patient confirmation/matching, DOB mutation and private notes remain
unsupported here. Existing native mount, admission, source start and capture
operations reuse their established facade; source availability still requires
the consumer and every independent capture/Save gate. No live Start-to-Save
qualification is claimed by offline tests.

Independently review exact paired commits and preserve receiver-first rollout.
Owner must choose real authority/practice/cohort, actual Clerk/session policy,
schema/Entra/grants/network acceptance and the qualified image/configuration
pair before activation. No production access, SQL, Azure/configuration write,
image action or deployment occurs in this leaf.

Before deployment discard/revert the leaf. After separately approved deployment
stop new visit submissions and capture admissions/pickup, retain uncertain
creation keys, drain accepted work, and restore the qualified prior compatible
image/configuration pair with the native visit flag false. Preserve visits,
receipts, audit, mappings, keys, ciphertext and schema; no immediate deletion or
registry/CLI fallback. Security/data rollback needs a separate owner plan.
