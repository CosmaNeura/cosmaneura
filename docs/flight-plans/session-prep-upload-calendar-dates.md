# Preserve calendar dates in recording uploads

Tier 2: existing patient identification and visit-date input. No schema,
dependency, cohort, Scribe capture protocol, or environment change.

Live staging showed an upload control displaying September 24 while the saved
visit appeared as September 25 after midnight UTC. The visit display interpreted
the API's offset-free UTC timestamp as local time. An existing patient's midnight-UTC birthday
could also appear as the preceding day and be resubmitted under that wrong day,
potentially splitting the patient's history used by Session Prep.

Appointment inputs retain their clock time and UTC serialization. The sidebar,
visit header and prior-note preview now interpret offset-free appointment
timestamps as UTC before displaying the provider's local date. Birthdays are
parsed as calendar dates and submitted at midnight without a timezone. Scribe
capture timestamps, stored records and unrelated date formatting are unchanged.

Independent review caught that the initial midnight-only appointment submission
would exclude earlier same-day visits from Session Prep. Retaining original
encounter instants fixes that regression without changing source eligibility.

Verify the real form's submission under positive/negative UTC offsets and a DST
boundary. Verify that the upload command preserves UTC instants and reuses an
existing patient with the same birthday, and that an earlier reviewed same-day
upload appears in the source selection and prior-note overview. Run upload/review
regressions, types, changed-file lint and a production-bundled frontend build.
Obtain independent agent review after the final substantive push; the user
approved a same-task reviewer while Cursor is unavailable.

Release through the next selected combined Core/Scribe candidate. Recovery
restores the previous application image; no stored records are rewritten.
