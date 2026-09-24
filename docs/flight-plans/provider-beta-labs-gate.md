# Keep Core reference labs outside the operational provider beta

## Outcome and boundaries

The provider assistant already excludes labs from its release capability set.
Core's separate reference toolkit still admits its staging cohort to lab entry,
interpretation, range editing and PDF extraction. An admitted medication-toolkit
user can therefore reach labs without enabling any assistant lab capability.

Add an independent, default-off `REFERENCE_LABS_ENABLED` server latch and
`VITE_REFERENCE_LABS_ENABLED` frontend build latch. Both must be explicitly true
for a future lab pilot, and the existing reference-toolkit cohort still applies.
Keep both absent/false for this provider beta. They do not enable the separate
Sovereign/EHR lab integration; `FEATURE_ATHENA_LABS` remains false there.

Tier 2: this narrows access to provider lab data and the extraction worker.
Medication/reference tools, note processing, Scribe, Session Prep, authority,
cohort membership and stored lab records are unchanged. There is no migration,
new dependency, service, model call, or live configuration change.

## Source and release

Base: Core `abe76316e954f7d44381bcf537f498d11a4df448`, including the separately
merged staging reconciliation work through PR490. This work uses its own
checkout; the original workspace and staging release checkouts are untouched.
It is independent of Core's reviewed panel pin in PR491. Include both changes
in the next coordinated Scribe/Session Prep release.

The API requires the lab latch and cohort on all nine Core reference-lab
operations, including the signed upload grant. A stale browser cannot bypass
the server gate. The worker checks the latch before loading a panel or touching
blob/model services; enabled workers also recheck the current toolkit cohort.
Disabled queued jobs return without modifying their pending record. They are
not automatically replayed when a later lab release is enabled.

The UI hides the Notes lab panel and Settings lab ranges independently of
medication tools and NatMed settings. The frontend flag is presentation only;
it grants no API access. Absent flags keep both local and deployed labs off.

## Validation and recovery

Use local checks because GitHub Actions is disabled. Test all nine endpoints
with release-off/provider-admitted and release-on/provider-unadmitted, verifying
404 and no upload/queue side effects. Keep existing lab operation tests behind
an explicit test-only opt-in. Verify disabled workers preserve pending uploads,
enabled workers enforce cohort, and medication and view-as behavior still pass.
Check Notes rendering, shared UI gates, TypeScript and a production frontend
build. Obtain independent Cursor review after the final substantive push.

During the separately managed rollout, align Core web/worker/scheduler on the
reviewed image with `REFERENCE_LABS_ENABLED` absent/false. Build the frontend
with `VITE_REFERENCE_LABS_ENABLED` absent/false. Old workers that started under
an enabled release can finish in-flight work; finish/drain those workers before
claiming the release is fully disabled. Verify Notes/Settings omit labs and
authenticated direct lab calls return 404. No live shutdown occurs in this PR.

Recovery: retain the release latches as false and restore the preceding
coordinated image if needed. A preceding image without this gate can expose
the legacy toolkit again, so that image is not evidence of a labs-disabled
beta. Restore the gated image to regain that invariant. Do not delete lab
records, uploads or ranges as rollback; leave queued/pending work dormant.
