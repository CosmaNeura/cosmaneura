# Preserve calendar dates in recording uploads

Tier 2: existing patient identification and visit-date input. No schema,
dependency, cohort, Scribe capture protocol, or environment change.

Live staging showed a date-only upload control displaying September 24 while
the saved visit appeared as September 25, after midnight UTC. The form converted
its local Date value to a UTC instant. An existing patient's midnight-UTC birthday
could also appear as the preceding day and be resubmitted under that wrong day,
potentially splitting the patient's history used by Session Prep.

The recording-upload form now serializes the calendar day shown in each control
as an ISO naive datetime at midnight, accepted by the existing API and database.
Patient birthdays are parsed and displayed as calendar dates. Actual timestamp
APIs, Scribe capture timestamps, prior records and global date formatting are
unchanged. This does not repair historical dates automatically.

Verify the real form's submission under positive/negative UTC offsets and a DST
boundary. Verify that the existing upload command persists the selected day and
reuses an existing patient with the same birthday. Run existing upload/review
regressions, types, changed-file lint and a production-bundled frontend build.
Obtain independent Cursor review after the final substantive push.

Release through the next selected combined Core/Scribe candidate. Recovery
restores the previous application image; no stored records are rewritten.
