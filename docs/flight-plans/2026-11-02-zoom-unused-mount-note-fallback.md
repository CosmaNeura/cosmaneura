# Zoom note preparation after an unused Scribe mount

Tier 2: clinical-source ownership. No schema, auth/session, cohort, configuration
or live-data change is included. Source is prepared against protected Core master
04281b86410a573fbea0c604f134206e35a5a081. Independent review and actual focused
PostgreSQL checks remain prerequisites to source integration and deployment.

A failed Scribe entry can leave a mount binding before any consent/session or
source claim. The legacy summarizer treats that unused binding as capture-owned
and returns even for an independently authorized Recall recording. This change
permits only the existing two-identity staging grounded-note policy to prepare
that one canonical Zoom transcript when the mount is unused. It does not merge
other recordings. The canonical Recall content receipt and current durable Zoom
recording/occurrence must still match the doctor, patient and appointment.

Any admission phase, published source, retained capture edit/context/fence or
current clinical note blocks the exception, including uncertain attempts and
revoked bindings. A queue hint alone cannot release ownership. The shared
appointment lock rechecks this scope before publication; normal Scribe admission
and source creation take the same lock, so a Scribe claim arriving during model
work prevents the Zoom note from being written. Existing automatic paths and
capture-authorized finalization retain their original behavior.

Qualification is one focused PostgreSQL regression file: unused mount permits
actual generated-note persistence; a pending admission blocks generation; an
admission arriving during model work blocks publication; existing edits and
published Scribe delivery stay retained; an unselected policy hint cannot opt in.
The prior source must fail the unused-mount case. Model/provider calls are stubbed
with synthetic content; no Zoom meeting, credentials or production rows are used.

Rollback restores the original unconditional capture-binding guard. It removes
no data or structure. This source change does not enable HANDOFF or NOTES flags,
start a worker, broaden the staged policy cohort, permit failed admitted captures
to be reused, or claim successful live recording/transcript/note qualification.
