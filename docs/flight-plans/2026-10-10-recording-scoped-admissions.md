# Recording-specific admission for saved clips

Tier 2: capture session contract and additive schema. Owner approval in the CosmaBot conversation on 2026-10-10 covers Core/Scribe implementation and matching invented-data staging only.

Keep the existing creator/tenant/patient/visit binding. Add a stable SHA-256 recording identity beneath it. Old journal rows and old requests select `legacy`; their digest stays byte-identical. Each new recording has its own consent/session keys and immutable reservation. Exact retries recover the same journal. A different consent payload conflicts rather than rewriting a saved attempt.

Receiver first: expand Core admission uniqueness and session-local source provenance; update guards to use binding plus session without weakening creator, revocation, transcript-delivery or immutable-history checks. Operation requests select an optional expected session; existing clients retain the legacy selection. Note requests already require expected session and now carry it through every nested pre/post-I/O authorization.

Next, update the OS sender to select a stable source identity and resolve retained transfers by exact recording/session. Expired unadmitted attempts need a separate explicit consent decision only after a definite no-admission result. Ambiguous outcomes retain the original request. Do not rewrite consent timestamps, transfer intent or audio.

Run disposable local PostgreSQL admission, concurrency, lost-reply, wrong-scope, old-journal and provenance checks. Rehearse the actual upgrade over retained fictional rows and compare their original columns. Review the final tested commit locally and record evidence in a PR before merging.

Before synthetic staging rollout, observe current source SHA, image digest, configuration and schema. Preserve unrelated changes, build the exact merged outcome, perform the approved additive migration through the deployment job rather than direct Core database access, and activate receiver before sender. No production promotion or live patient data is authorized.

Recovery: disable the new sender behavior and select the prior image with the capture facade disabled where required. Retain the additive column, admissions and source journals. An old receiver that assumes one row per binding cannot safely serve bindings with new admissions; do not pretend image rollback alone is sufficient. Never drop the expand structure or delete a recording to recover.

Acceptance remains the actual CosmaBot saved audio → encrypted upload → transcript review → edited note → saved/reopened note, repeated for optimization and first-time-provider usability. Scripted service walkthroughs do not establish that acceptance.
