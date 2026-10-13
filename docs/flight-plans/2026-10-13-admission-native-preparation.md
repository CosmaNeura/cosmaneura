# Return native recording preparation with fresh consent

Tier 2; cross-repository native session and consent path. Owner: John. The
existing personal fictional staging cohort is the initial validation scope.
This implements the approved startup work reduction without changing consent,
source authorization, tenant selection, encryption, reservations or saved notes.

The additive Core `prepared_admission` opt-in includes only the existing closed
native preparation receipt after consent/session creation and its unchanged
fresh authority/read/publication checks. Core advertises a versioned capability.
Scribe requests it only for a retained scoped mount that advertised support.
Older Core uses ordinary admission followed by the original fresh native read
inside the same device request. Neither path retries an uncertain admission.
No credential or access decision is returned to the device.

The native bridge opts in for fresh non-null consent only when our receiver
advertises support. It validates the closed reply, same workspace/binding/session,
current native account, key shape and server timestamp, and strips native metadata
before renderer publication. One in-memory context transfers once to the next
recorder preparation. Preserve its original monotonic expiry of at most five
seconds through that transfer. Expiry, cancellation, replacement, auth changes,
malformed replies and mount changes cannot retain an old context. Native source
permission still runs freshly on Start. No audio is captured by qualification.

Release order: exact-final-commit independent local review and conditional merge;
then the merged Core receiver via the existing founder staging workflow; then
merged Scribe receiver and a reviewed native beta build. GitHub Actions remains
disabled, and no agent performs direct Core deployment or enables a workflow.
Compare the immutable deployed source/digests before promotion. No dependency,
configuration or migration is introduced. Existing TestFlight groups remain the
only intended groups. This code is dormant in installed build 64.

Acceptance: actual synthetic SQL consent creates exactly once and native metadata
matches the separate read. Revocation/lost replies expose no partial result. Real
receiver registry checks still fence dispatch and publication. Actual native
macOS/iOS source compiles; offline tests cover metadata rejection, single-use,
expiry without extension, cancellation, account/mount changes and compatibility.
Three native fixture comparisons must show one fewer request and SDK token read;
injected network delays prove work removal, not physical phone speed. Repeat a
fresh fictional recording → transcript → saved-note smoke test and fresh
consent-to-first-audio phone timing before declaring the five-second goal met.

Recovery: restore the prior Scribe receiver digest and prior beta build first;
then restore Core if needed. Older clients do not request the optional receipt;
new clients/receiver retain the original pre-negotiated fallback. Keep all
consent evidence, recordings, transcripts and notes. No data reversal is needed.

Workspace evidence: output/scribe-provider-ux-20261007/native-admission-round-20261013.
Independent review of the final substantive push must be linked from each PR.
