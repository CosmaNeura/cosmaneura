# Transcript refresh type preservation

Tier 2: this fix touches the patient transcript model. No schema or stored-data
change is involved.

SQLAlchemy refresh and expiration can reload transcript segments as dictionaries.
Scribe source validation and note editing expect `TranscriptEntry` objects and
can then raise an exception. Restore typed entries on refresh as well as initial
load, and accept already-parsed entries when ActiveModel repeats conversion.
Use committed assignment so reads do not become edits; a partial refresh of
another field must preserve unsaved transcript changes.

Validation covers initial load, explicit refresh, expiration, ActiveModel save,
and partial refresh with an unsaved edit. Scribe import and provider review HTTP
tests exercise existing callers. The combined beta rehearsal additionally
verifies capture, review, a subsequent Session Prep brief, correction, stale
brief rejection, and a newly reviewed replacement.

Release this through the shared Core image after independent Cursor review.
Preserve the current Scribe source checkpoint when assembling that image; this
PR alone does not authorize replacing an active staging build. No migration,
backfill, feature flag, or external-service change is needed. Recovery is a
normal revert and Core image rollback; transcript rows remain unchanged.
