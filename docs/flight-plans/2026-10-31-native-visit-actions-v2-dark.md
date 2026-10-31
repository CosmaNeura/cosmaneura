# Full native visit actions v2 — dormant source, Tier 2

The ordinary Scribe name-only first visit needs provisional patient creation,
explicit patient matching/confirmation with optional DOB, and author-only private
note read/save. The existing Core writers already implement those operations.
This receiver-first leaf exposes only that closed set through the current dual-auth
native encounter facade, without a second writer, schema, dependency or service.

`X-Cosmaneura-Scribe-Encounter-Version: 1` retains the existing-patient subset and
its v1 response. Explicit version `2` additionally permits `create` with the existing
`NewPatient` contract, `patient_match_candidates`, `patient_match_save`,
`private_note_read` and `private_note_save`. Both versions permit `patients` and
existing-patient creation. Version2 does not permit catalog/lookup, legacy
`add_birth_date`, or any generic operation. Its outer response/header version is2;
the reused domain writer remains v1.

`SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED=false` is the default. Selecting it requires
the existing native workload, provider-context and existing-visit prerequisites.
The full gate is re-read at entry, by the unchanged writer's commit-time settings,
and before returning. Browser encounter, global Agent API/Authority Kernel,
Session Prep, capture, notes, worker, consent and source controls stay separate.
Creating a visit neither records consent nor starts audio.

The verified Clerk session and singleton workload precede body dispatch. Current
Core context owns actor/tenant; provider/practice/member locks, doctor-owned visit
and canonical patient checks remain in the existing services. The closed body parser
preserves4096-byte ordinary and16384-byte private-save limits, exact creation/change
key equality, UTF-8/private bounds, version conflicts and retained receipts.
Private text remains encrypted and separate from clinical notes, transcripts and
exports. The existing keyed private receipt contains metadata only; identity/create
mutations retain their existing metadata audit events. Final current authority and
commit-time availability checks remain intact.

Provider-context callers may explicitly send `include_native_visit_actions_v2:true`.
Only those replies add the strict boolean `native_visit_actions_v2`, reflecting
the current full gate. Requests without the opt-in retain the original response
shape and connection/recovery digest. This capability grants no authority.

The paired Scribe source leaf adds a default-off receiver opt-in and closed v2
transport. It must retain `existing_patient_visits_v1` for old signed clients and
give a confirmed full marker precedence only in explicitly parsed Production
clients. Merely removing the old marker would select the current Staging parser.
Ship/qualify Core before Scribe; do not advertise full mode before all five actions
and matching gates are available. Native renderer changes need new signed-source
provenance and installed-client acceptance; prior signed candidates do not prove
this full flow.

Focused evidence is offline synthetic route/service dispatch, v1 compatibility,
capability shape/strictness, current authority, gate rechecks, exact keys and existing
creation retry behavior. It is not live schema/grants, patient mapping, installed
client, clinical fidelity or Start-to-Save acceptance. Independent review after
the final substantive push and current required checks precede merging.

Source recovery is revert/discard this leaf or keep v2 dark while retaining v1.
After a separately approved deployment, stop new v2 submissions through its
control and restore the prior compatible pair. Retain uncertain keys, accepted
visits, private/identity records, audit/receipts, mappings and ciphertext. Do not
delete data or use a registry/global API fallback. Production configuration,
schema application, grants, cohort activation, image publication, migration and
deployment remain reserved owner decisions; this leaf performs none.
