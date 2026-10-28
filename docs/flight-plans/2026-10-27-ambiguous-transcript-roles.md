# Preserve ambiguous transcript roles

Tier 2: this changes clinical transcript attribution. No schema, authorization,
deployment or existing-record change is included.

A single Zoom participant can carry multiple voices. The existing classifier
assumes exactly one doctor and one patient, so it incorrectly assigns a clinical
role to every word from that shared participant. More than two labels similarly
collapse everyone except the guessed doctor into Patient.

For new transcription only, non-two-label sources retain all words and timing
with distinct qualified source labels and make no role-classification calls.
Original source speaker IDs, when present, are retained on both paths. Labels
such as `Patient` are qualified too, so a display name cannot establish a role.
Exactly two labels use
the existing model path; an out-of-range result fails instead of assigning every
entry to Patient. This does not identify acoustic speakers or verify any role.

Recovery: revert this change before the next transcription. Existing saved
transcripts and notes are not regenerated. Validate one/shared/multiple-label
recordings, two-label behavior, unchanged source text/timing, and zero calls for
ambiguous source counts. Independent final-source review remains required.
