# Provider question reviewed sources v1.1.0

Opt-in extension of the existing governed provider question reader. The v1.0.0
request, result and receipt digest remain unchanged. Request tool_version=1.1.0
with detail=reviewed_sources; all other request fields and headers remain v1.
The existing provider/encounter capabilities, reviewed-note selection, cohort,
source locking, 24-hour receipt retention and final acceptance rules apply.

READY adds reviewed_sources to the v1 result, with schema_version=1.1.0. Each
source contains source_ref, source_version, source_at and sections (an ordered
mapping of note section title to text). Its source/version/date must exactly
match the included provenance. Only the still-current reviewed snapshots in
the durable brief's current source selection are returned. Raw transcripts,
drafts, other patient records and unselected notes are excluded.

At most five sources are returned. Each has 1–64 sections and shares the review
adapter's 18,000-character canonical JSON bound, for at most 90,000 characters
in aggregate. Titles must be nonblank, titles/text cannot contain NUL, at least
one section text must be nonblank, and the canonical representation must encode
as UTF-8. These aggregate/lineage constraints are enforced by the runtime in
addition to the JSON schema. No source text or model exception is logged.

PENDING and UNAVAILABLE return reviewed_sources=[] and no prepared content.
An existing receipt cannot change version, detail, state, sources or content.
Source correction or revocation invalidates both final acceptance and replay.
The final writer and conversation history distinguish v1.1 receipt markers
from v1 markers and validate the complete returned source evidence.

Deploy this receiver before its consumer. An older receiver returns 404 with
code RESOURCE_NOT_FOUND for an unsupported tool version, before creating a
receipt. A consumer may fall back to the v1 summary for that exact unsupported
response only; authentication, conflict and availability failures must not
trigger downgrade. The consumer must disclose whether it used selected reviewed
notes or a preparation summary. Neither is a full chart or EHR read.
