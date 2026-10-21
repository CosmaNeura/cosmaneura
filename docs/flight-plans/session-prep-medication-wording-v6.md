# Session Prep medication history wording — Tier 2

## Problem and scope

The v5 prompt prohibits medication-action verbs, while the unchanged egress
guard also rejects the standalone words `start`, `stop`, `increase` and
`decrease` in historical nouns, negations and discussion questions. A recorded
"planned increase ... dose" therefore fails the same guard as a new medication
instruction. Live metadata reported a medication-category rejection on a prior
attempt; the exact rejected wording is unknown, and a later running attempt is
not a successful brief.

This Core-only increment makes the initial and category-specific retry prompts
match that existing lexical rule. It supplies dated neutral dose/status forms
for every brief section, preserves original and corrected doses and explicit
uncertainty, and distinguishes planned use, documented use and undocumented
confirmation. It adds no model, service, configuration, API or schema change.
The prompt version becomes `source_only_prep_v6` so existing v5 artifacts and
replay receipts cannot be represented as a v6 preparation.

## Safety and validation

Keep every existing egress pattern, citation check, reviewed-source selection,
ownership/freshness fence, noncompletion grounding rule and output bound. A
rejected first draft is discarded. At most one new draft is requested from the
unchanged original reviewed notes, within the original 50-second model-work
budget and 25-second per-call maximum; the unsafe draft is never transformed or
retransmitted. A second rejected draft stays unavailable.

Focused offline regressions exercise noun, negated and discussion wording in
all six brief sections; the existing medication rejection code remains `04`.
Fake SDK recovery must retain dated dose history, correction, documented use,
uncertainty, source citations and numerical observations. Tests also require
second-draft rejection, unchanged original source payloads and a different v6
digest for identical sources. The database-backed cache/replay regression uses
an explicit v5 predecessor. The two actual runtime controls passed in separate
new synthetic PostgreSQL databases: a ready v5 artifact and replay are accepted
before the version change, v6 rejects the old replay, and a fresh v6 artifact
version 2 supersedes v5. The isolated runner denies outbound requests, avoids
global conftest cleanup, and leaves every existing database unchanged.

## Release and recovery

1. Independently review the final substantive pushed commit and its exact
   focused test evidence. Reviewed PRs may merge after inspecting current-master
   overlap while Scribe/Core work proceeds. This checkpoint performs no automatic
   deployment or deployment pause.
2. Bind that future update to its actual merged source, independently accepted
   immutable image and compatible staging baseline, including the latest combined
   source and preserving concurrent Scribe advances. Never restore or rebuild an
   obsolete Core snapshot over those advances. Do not alter native Scribe,
   Sovereign, provider cohort, configuration or database for this increment.
3. In staging, request a fresh brief using authorized fictional reviewed visits
   with recorded medication use, an earlier dose plan and a later correction.
   Verify the actual accepted artifact is v6, both dated doses and citations
   remain present, no new treatment instruction appears, and the surrounding
   structured observations and reviewed-source questions still work.
4. If v6 remains unavailable, retain the closed failure metadata and investigate
   without weakening the gate or blind retries. Recover by reverting this
   prompt/test increment in a later reviewed frontier-aware Core image; preserve
   the existing correction-frontier schema and acknowledgement data. An old
   pre-frontier image is not a compatible recovery.

Offline tests prove prompt delivery, deterministic validation and bounded fake
SDK behavior. They do not prove live model compliance or operational beta
readiness; live efficacy remains unverified until deployed and checked.
