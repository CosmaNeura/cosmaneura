# Optional birth dates for Scribe encounters

Tier 2: patient schema and cross-repository patient-entry flow. Owner requested
optional DOB for impromptu Mac/iPhone Scribe visits on 2026-09-30. Production is
excluded. Root task owns merged-candidate promotion and native delivery.

Core accepts an omitted or null `patient.date_of_birth` for explicit new-patient
Scribe creation and stores SQL NULL. A supplied date keeps its existing range
validation. Names never select an existing patient: that remains an exact,
currently authorized patient reference. The unchanged creation key/body receipt
retains retries; old non-null request digests stay identical. Omitted and explicit
null normalize to the same new request body. No placeholder or inferred date is
written. Patient search returns `date_of_birth: null` for unknown dates.

Expand rung: Alembic-generated revision `16a6bf2d1539`, parent `de0c6b95f69f`,
changes only `patient.date_of_birth` from NOT NULL to nullable. No backfill, index,
identity key, authority, consent, recording or retention changes. The synthetic
fixture and identity-rebind guards explicitly reject an unknown fixture DOB.
Legacy file upload still requires an entered DOB for its existing matching
behavior; selecting an unknown-DOB patient clears any prior date and displays
that it was not provided. Appointment selection continues by exact ID.

Migration SQL lint has zero errors and the expected `ban-drop-not-null` warning:
older clients may assume a date is present. The compatible receiver/reader-first
sequence below addresses that warning; it is not a claim that old readers become
safe automatically. The nullable expand does not rewrite existing dates.

Receiver-first staging sequence:
1. Record current deployed source/image/config/schema and active native versions.
   Keep new optional-DOB clients inactive during the expand and receiver rollout.
2. Apply the merged nullable expand with the reviewed staging migration runner;
   inspect resulting nullable metadata. No database-wide backfill is needed.
3. Deploy the compatible Core receiver and website together, including null-safe
   existing-patient readers and the generated API contract. Verify deployed
   null-capability and nullable metadata read-only, plus known-DOB compatibility.
   Do not create a live null-DOB patient yet: old native catalog parsers fail on it.
4. Install null-capable Scribe clients in both idle same-identity Mac and iPhone
   bundles while preserving saved requests and recordings. The existing generic
   Scribe receiver already passes this contract through and needs no DOB-specific
   deployment. Only after both installed clients are verified null-capable, create
   the first controlled synthetic omitted-DOB patient; verify lost-response retry,
   patient lookup, another visit for that exact patient, and provided-DOB creation.

Recovery: disable new optional-DOB entry if necessary. Retain the nullable column,
all patients/appointments/receipts and compatible null-safe readers. Once unknown
dates exist, restoring an old reader that assumes DOB is unsafe. The migration
deliberately refuses downgrade; do not synthesize dates, delete rows or reapply
NOT NULL as rollback. Correct forward or deploy an explicitly compatible build.

Local rehearsal uses only the owned local databases named
`scribe_optional_dob_migration_test` and `scribe_optional_dob_workflow_test` on the
pinned Unix-socket test harness. Each migration run creates a fresh isolated
schema and retains previous runs' fixtures. It proves existing known DOB values survive the
expand, null rows persist, metadata matches the model, and rollback refusal
retains both. Synthetic workflow tests cover null/omission normalization, exact
patient reuse, duplicate/retry behavior, invalid supplied dates, current authority
and support-view denial. UI tests cover unknown labels, stale date clearing, and
the unchanged legacy upload requirement. No live recordings or data are used.

Generated contract: export the actual internal API OpenAPI with the locked Python
runtime; run the repository-pinned `@hey-api/openapi-ts@0.97.3` generator and route
typegen. `web/openapi.json` is tracked; generated `web/client` files remain ignored
as configured. Record the exact tested/reviewed commit, commands, results and
reconciled immutable staging/native artifacts in the delivery evidence.
