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

Independent review identified that retry also reruns the authenticated and notes
ancestor loaders. Both now have a whole-operation deadline. Settings or status
timeouts hide the provider outlet; list timeouts show a retryable visits error.
Expired shared queries are cancelled by exact key so late responses cannot
repopulate the cache. Navigation cancels only the departing loader's wait.
Support-view status reads are bounded before binding observation, including
the enclosing error guard, so recovery cannot move the stall into that guard.

Verify missing/other-provider/own-record HTTP behavior, safe status mapping,
deadline and cancellation, exact-note focus, and a real router retry/navigation
cycle. Run the relevant prior-note, source-review, layout/guard regressions,
types, changed-file lint and production-bundled frontend build. Include real
ancestor-loader regressions for stalled status, settings and lists, late results
and successful subsequent retry. Obtain independent agent review after the
final substantive push; the user approved a same-task reviewer while Cursor
is unavailable.

Deploy with the next selected Core/Scribe candidate after source reconciliation.
Recovery restores the prior Core image; no data or schema is rewritten. Live
staging acceptance uses only the explicitly approved fictional recordings.
