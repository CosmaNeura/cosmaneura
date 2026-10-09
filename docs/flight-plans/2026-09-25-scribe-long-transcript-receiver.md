# Receive complete long-visit transcripts

Tier 2: existing protected transcript/review contracts. Owner requested visits
up to three hours and authorized implementation, local validation and merge.
This receiver increment has no migration, live data write or deployment.

Accept up to 500,000 total source/correction characters while retaining the
100,000-character individual-segment and 10,000-segment limits. This gives
headroom over 270,000 characters at 250 words/minute, six characters/word and
180 minutes; elapsed duration alone does not bound transcript text. Raise the
creator-review response bound from 8 to 32 MiB to cover source, translation
review and provenance metadata, including escaped Unicode JSON. Use the
existing 24 MiB source-response limit for raw transcript reads too. Increase
working responses to 24 MiB and the outer Core response to 32 MiB+8 KiB.
The 10,000-segment review fixture measures about 29 MB when escaped and remains
under its byte guard. Do not truncate an oversized transcript.

Rollout: Core receiver first; then Scribe/native review receivers; then Scribe
aggregate validation and database constraints. Keep individual text limits,
immutable source/revision hashes, exact translation provenance and review gates.
Do not enable larger writers until every receiver and persistent constraint is
compatible. Existing model input/output guards and model remain unchanged.

Recovery: prior to admitting larger text, revert the receiver release normally.
After larger text is retained, keep compatible readers and disable new larger
writes; never downgrade readers in a way that strands saved recordings/notes.
No key, session, terminal job or retained record is reset.

Validation: bounded maximum-size Unicode review/raw transport, exact aggregate
and segment rejection boundaries, dense three-hour synthetic text through real
local Core persistence and both existing model prompts using a deterministic
model double, edited-note save/reopen and source-revision regression tests.
Model-double tests prove source delivery, not generated clinical accuracy or
cloud model latency. Record exact tested SHA and implementer review in the PR.
