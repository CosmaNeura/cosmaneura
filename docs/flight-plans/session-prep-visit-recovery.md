# Session Prep visit loading and recovery

Tier 2: existing provider-record reads and the dashboard error path. No schema,
dependency, cohort, identity, or deployment-setting change.

Opening a saved prior-note link under another signed-in account currently
produces an unhandled lookup failure and a generic crash screen. Missing visits
and visits owned by another provider must return the same no-store 404 response.
The existing doctor ownership predicate remains in force.

The note loader bounds token acquisition, transport and parsing to 30 seconds.
Navigation aborts the request and late responses cannot replace a recovery view.
Expected read failures display fixed, content-free guidance with an explicit
retry and a return-to-visits link. The recovery page mounts neither patient data
nor the assistant. Existing practice/view-as recovery and unexpected rendering
failures retain their enclosing guards. No automatic read retry is added.

Verify missing/other-provider/own-record HTTP behavior, safe status mapping,
deadline and cancellation, exact-note focus, and a real router retry/navigation
cycle. Run the relevant prior-note, source-review, layout/guard regressions,
types, changed-file lint and production-bundled frontend build. Obtain Cursor
review after the final substantive push.

Deploy with the next selected Core/Scribe candidate after source reconciliation.
Recovery restores the prior Core image; no data or schema is rewritten. Live
staging acceptance uses only the explicitly approved fictional recordings.
