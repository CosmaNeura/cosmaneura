# Zoom terminal capture status

## Outcome and boundaries

An owned synthetic meeting reached Recall `in_call_recording`, then host recording permission was denied, followed by `call_ended` and `done`. Redis still claimed `recording_active`. This change accepts the four signed terminal/denial bot events, binds them to the bot published by the existing authorized launch, clears active state atomically, and exposes a scoped warning in the schedule response and meeting detail. `capture_started`, the consumed authorization, bot identity and recording-to-visit bindings remain intact. No automatic retry, provider call, new capture authority, transcript change or clinical content is added.

## Risk and dependencies

Tier 2: the existing launch token fence gains a stop condition. No schema or migration is needed. All existing scope checks remain; bot publication creates a 24-hour reverse binding using server-owned doctor/meeting/occurrence data. Events never use provider-supplied metadata to infer ownership. New launches cannot publish recording after a terminal event. Denial and failure are retained across duplicate/out-of-order delivery and later `done` events.

This correction is separate from any change to pre-Start host-approval behavior. Public Recall manual-recording documentation says to create with `recording_config: null`, then Start Recording after the bot joins; the legacy permission settings in our payload are absent from the current public ZoomRequest schema. Do not change that contract or infer that early `in_call_recording` proves durable host permission without an independent supported-flow review.

## Current deployed state

Base: Core master `97614048041a0de6b50cd43dea6502d956a26f03`. Staging observation: web revision72; actual18 preserved the 89.234718-second completed recording and exact visit mapping, followed by stale active flags. The bot captured no controlled fixture audio. This is failure evidence, not a successful acoustic/clinical run.

## Rollout and promotion

Deploy the receiver and worker together to synthetic staging before selecting the four additional Recall webhook subscriptions: `bot.recording_permission_denied`, `bot.call_ended`, `bot.done`, `bot.fatal`. Verify the actual subscription configuration and successful signed inbox delivery; source acceptance is insufficient. Previously launched bots have no reverse binding and remain unresolved rather than adopting provider metadata. Use a new normal authorized synthetic meeting for qualification.

The schedule refresh reads the scoped result; this change does not add polling. The projection is live-occurrence fenced. Events delivered after the meeting's live keys have been cleared cannot alter them; a previously observed result remains readable against the consumed authorization for 24 hours. Delayed/missing webhook reconciliation and a full recording health monitor remain follow-up work.

Canary gates: actual host denial clears active state while keeping `capture_started`; no second Start occurs; schedule detail shows denial; a subsequent done event retains the reason; a separate authorized successful meeting yields exactly one normal recording and delivered note. Also verify the new initial binding does not disrupt preserved live desktop/mobile sessions. No production promotion is selected by this patch.

## Recovery and completion

Hold new synthetic launches if terminal delivery fails, scope changes affect another occurrence, or any automatic retry occurs. Restore the prior web/worker image together and restore the previous subscription set; do not delete retained recordings, consumed authorization or the single-start fence. Additional Redis keys expire naturally. There is no data migration or irreversible cleanup.

Completion requires independent review of the final commit, passing route/real Redis/frontend tests, exact staging deployment/subscription receipts and the controlled canary gates above. A local warning panel does not establish clinical usefulness or a deployable Zoom companion.
