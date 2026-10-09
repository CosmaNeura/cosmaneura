# Source-extractive prospective plans in new Scribe drafts

Tier 2: clinical note generation. One Core code release; no schema, public API,
dependency, credential, cohort, or stored-template change. Root owns publication
and staging activation. No production or physical-device actions in this work.

## Observed defect and boundary

The authorized iPhone55 synthetic cough transcript states duration and nighttime
frequency, without a treatment decision. Core generated monitoring and advice to
seek medical attention. The existing system prompt already forbade unsupported
plans, and capture already disabled the legacy inferred-plan-reason option. The
remaining validator checked JSON shape only. A stronger prompt alone is not the
fix. The committed fixture preserves the actual 27 field names, synthetic
transcript and generated values; original schema guidance was not exported.

For new capture drafts, recognized prospective headings (plan, recommendations,
follow-ups, return precautions, patient instructions, next steps, and the listed
Spanish equivalents) use a bounded source-reference contract in the same model
call. Core ignores generated prose for those fields. It emits complete unchanged
referenced utterances, in source order, with their admitted speaker role, or the
complete separately attributed saved scratch note. Unknown voices never become
the signed-in clinician. The label is `Transcript` because the authoritative
text may be reviewed English or a correction, not verbatim source audio.
An empty reference list becomes `Not mentioned in
transcript`. Invalid, missing, duplicate, out-of-range or noninteger references
fail closed before note finalization. Selected source text is also subject to the
existing complete-note byte limit.

The authoritative source and notebook revision remain bound by existing
fingerprint/consent/authority checkpoints before publication. No second model
call, generated intermediate action list, unchecked fallback or automatic retry
is added. Public field names, string values, hashes, and draft receipts retain
their existing shape. Saved drafts, old notes, explicit clinician edits and
ordinary HPI formatting are not rewritten by this guard.

## Limits that must remain visible

The model can still select an irrelevant passage or omit relevant context; the
guard guarantees source words, not correct clinical classification or complete
recall. A regression deliberately selects a complaint and proves that it stays
an attributed complaint rather than becoming medical advice. All drafts still
require clinical review. Free-text fields outside the recognized prospective
headings retain existing generation behavior. Arbitrarily named custom fields
need an explicit semantic field contract before they can receive the same
guarantee. Source-reference output adds prompt bytes and remains within the
existing bounded request limit. Source indexes are inserted into the existing
transcript block after each speaker label; scratch notes are indexed in their
existing block. The model receives each source once. The shared builder's
default output remains byte-identical. Model compliance and physical acceptance have
not been measured by local deterministic model fixtures.

## Template label evidence

The stale dates/assertions are persisted `DoctorNoteSchema.fields` keys, copied
into the receipt and rendered verbatim. They are not generated note values:

- `Exam deferred to in-person visit 08/26/2026`
- `Labs - fasting, 02/12/2026`
- `Return precautions - given verbally, repeated back accurately, sent in writing`
- `Observed and patient-assisted exam via video`

This change preserves the schema and existing notes. A separate display-only
treatment can show `Deferred examination`, `Laboratory tests`, `Return
precautions`, and `Video examination`, while keeping the exact original heading
inspectable and using the original key for saves. The existing provider template
editor remains the explicit route for changing a stored template.

## Validation and recovery

Use the isolated authority test subtree with the previously verified disposable
PostgreSQL fixture and locked Python runtime. Cover the actual 27-field failure,
explicit/negated/conditional plans, source languages/translated English, current
medication history, complaints/questions, scratch-note attribution, invalid
references, normal reads/replay, concurrent authority/source changes, and saved
note edits. Run focused Ruff, Pyright, and diff checks. Exact commands, source
hashes and results belong in the local evidence and PR.

Release Core first; existing clients already accept these ordinary string field
values. Existing drafts are not regenerated. A model-format failure retains the
source and publishes no note. If preparation becomes unavailable, use the
existing capture-note feature gate to stop the workflow while fixing the
contract; this gate can also temporarily disable Scribe note reads, but retains
all records. Do not fall back to unchecked generated plan prose or rewrite prior
notes/templates. There is no migration or data rollback. New extractive draft
strings remain compatible with the prior reader/editor.
