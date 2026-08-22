# Zoom Flow — Manual Testing Guide (Phase 1)

Step-by-step E2E checks to run on **staging** before enabling the hardened
legacy Zoom bot. Goal: prove the single-segment, fail-closed lifecycle against
real Zoom, real Recall, and the real database. Same-bot recording resume stays
disabled until the provider-neutral capture flow can authorize an exact
encounter.

Each scenario lists: **Setup → Steps → Expected → How to verify**. If any expected step doesn't hold, capture logs + Redis snapshot and flag.

## Prerequisites

- Staging is deployed with the `feat/zoom-merge-window` branch.
- You have a doctor account on staging with Zoom OAuth connected.
- You have a second Zoom account (or a guest browser) to act as the patient.
- The chosen `ZoomMeeting` is already bound server-side to the exact
  appointment being tested, and has an unexpired authorization for the HMAC
  digest of this exact Zoom occurrence. The separate authorization is consumed
  before bot creation while `appointment_id` remains available for dashboard
  navigation. An unbound, mismatched, expired, or replayed authorization must
  not create a bot; the webhook path must never fabricate a placeholder patient
  or appointment.
- **Activation gate D2:** this branch intentionally does not create the trusted
  server-side occurrence authorization. Do not enable clinical capture until a
  separately reviewed scheduling/launch workflow populates the digest and
  expiry for the exact appointment and occurrence.
- A terminal with `redis-cli` access to the staging Redis (or the equivalent admin tool you use).
- Tail of the API logs in one window (`heroku logs --tail` / equivalent), filtered for `zoom_webhooks`, `merge_window`, `recall`, `summarize`.

### Useful Redis commands

```bash
# Replace MEETING_ID with the actual Zoom meeting id
redis-cli KEYS "recall:meeting:MEETING_ID:*"
redis-cli GET "recall:meeting:MEETING_ID:bot_id"
redis-cli GET "recall:meeting:MEETING_ID:recording_active"
redis-cli GET "recall:meeting:MEETING_ID:capture_started"
redis-cli GET "recall:meeting:MEETING_ID:occurrence_digest"
redis-cli GET "recall:meeting:MEETING_ID:merge_task_id"
redis-cli GET "recall:meeting:MEETING_ID:paused_at"
```

Key meanings:
- `bot_id` set → a Recall bot is tracked for this meeting (may be paused).
- `recording_active = 1` → recording is currently running.
- `capture_started = 1` → this meeting lifecycle has consumed its one
  permitted recording segment; it must not start or resume another.
- `occurrence_digest` set → lifecycle state is fenced to one HMAC-pseudonymized
  Zoom occurrence UUID, not merely the reusable meeting/PMR ID.
- `recording_active` absent + `bot_id` present → stopped and awaiting a
  confirmed bot leave.
- `merge_task_id` present → a 10-min release task is armed.
- `merge_release_intent_id` present → the task carries only an internal inbox
  ID; the encrypted database row retains the bot handle even if Redis is lost.
- A pending/retry `internal.zoom_bot_pause` or `internal.zoom_bot_release`
  delivery in the encrypted webhook inbox is a durable teardown intent. It
  means stop/leave/queue did not finish conclusively and the reconciler owns
  recovery; do not clear it or the Redis bot handle by hand.
- Participant display names and raw Zoom participant/user IDs are not retained
  in membership state. Ingress reduces the configured bot name to an `is_bot`
  boolean; Redis sets/hashes contain only occurrence-scoped HMAC references and
  signed `event_ts` ordering markers.

## Scenario 1 — Happy path (no pause)

Baseline. Confirms nothing regressed.

**Steps:**
1. Doctor starts the Zoom meeting.
2. Patient joins.
3. They have a short conversation (~1 min).
4. Doctor ends the meeting.

**Expected:**
- One bot created.
- One transcript saved.
- One AI visit note generated for the exact server-bound appointment.

**How to verify:**
- Logs: `appointment session started` (once) and `meeting ended with active recording`.
- App UI: the appointment shows a note with the conversation summarized.
- DB: `SELECT COUNT(*) FROM ai_visit_transcript WHERE appointment_id = '...'` returns 1.

## Scenario 2 — Doctor steps out briefly, patient stays

This exercises the deliberate fail-closed tradeoff.

**Steps:**
1. Doctor starts the meeting, patient joins. Recording starts.
2. Speak for ~30s so we have content.
3. Doctor leaves Zoom (close the tab or hang up). Patient stays in the room.
4. Wait 60–120 s.
5. Doctor rejoins and attempts to continue.
6. Doctor ends the meeting.

**Expected:**
- Bot does **not** leave Zoom while the doctor is out (you can see it still listed as a participant in the meeting).
- When the doctor returns, recording does **not** resume and no new bot is
  created. The provider must start a new Zoom meeting to capture more audio.
- The note contains only the first, explicitly started segment.

**How to verify:**
- Logs (in order): `participant left: doctor`, `recording paused, merge window armed reason=doctor_left`, then `recording continuation blocked without exact encounter binding`.
- Redis after doctor return: `capture_started = 1`, `bot_id` and
  `merge_task_id` present, and `recording_active` absent.
- DB: the exact server-bound appointment receives exactly one transcript.

## Scenario 3 — Doctor steps out for too long (>10 min)

The merge window has to actually expire and clean up.

**Steps:**
1. Doctor + patient in the meeting, recording active.
2. Doctor leaves. Patient stays (or also leaves — either works).
3. Wait **>10 minutes** without anyone rejoining.

**Expected:**
- After ~10 min, the bot disconnects from Zoom automatically.
- A note is generated from whatever was recorded before the doctor left.
- A new participant in the same still-running Zoom lifecycle cannot start a
  second recording, even after the bot has left.

**How to verify:**
- Logs: `merge window expired, releasing bot`.
- Redis after expiry: `bot_id`, `merge_task_id`, and `paused_at` are gone;
  `capture_started = 1` remains as the no-second-segment fence.
- App UI: a note is present for the appointment.

## Scenario 4 — Last patient leaves while doctor stays

Mirrors Scenario 2 but the trigger is the patient leaving, not the doctor.

**Steps:**
1. Doctor + patient #1 in the meeting.
2. Patient #1 leaves. Doctor stays.
3. Within 5 minutes, patient #2 joins.
4. Doctor finishes the visit and ends the meeting.

**Expected:**
- Bot stays connected after patient #1 leaves.
- When patient #2 joins, the bot does **not** resume, no second recording ID is
  created, and patient #1's appointment remains unchanged.
- The armed release task remains responsible for disconnecting the old bot.

**How to verify:**
- Logs: `recording paused, merge window armed reason=last_patient_left`, then
  `recording continuation blocked without exact encounter binding`.
- Recall: exactly one Start Recording operation and one recording ID.
- Redis: only `recall:recording:{patient-A-recording}:*` carries the first
  appointment binding; there is no bot-scoped appointment fallback.

## Scenario 5 — Back-to-back patients across the window (>10 min gap)

This proves that only an explicit new Zoom lifecycle reopens capture.

**Steps:**
1. Run Scenario 2 or 4 to completion — finish with a clean note generated for appointment A.
2. Wait >10 min for the merge window to expire and the bot to leave.
3. End the Zoom meeting, then start a **new** Zoom session (a new
   `meeting.started` delivery), even if it reuses the same PMR meeting ID.
4. A different patient joins.

**Expected:**
- A new bot is created (not the old one — it disconnected after the window).
- A new bot starts only after the new meeting lifecycle is bound server-side
  to patient B's exact appointment. No nearby appointment is selected by time
  or display name.
- A separate note is generated for the new appointment.

**How to verify:**
- Logs: a fresh `appointment session started` with a different `appointment_id` than the previous run.
- DB: the old appointment still has exactly one transcript and one note; a brand-new appointment row exists for the new patient.

## Rolling-cutover warning

The hardened transcript consumer accepts only
`recall:recording:{recording_id}:doctor_id|appointment_id|patient_id`. It no
longer accepts bot-scoped keys as encounter authority. Before deploying it,
drain callbacks for recordings created by a revision that did not write all
three recording-scoped keys, or explicitly reconstruct and review those exact
bindings. A bot-only in-flight callback will retry/fail closed and must be
mapped and redriven by an operator; it must never be made to inherit the bot's
last appointment. Do not run old and new capture behavior concurrently.

The Zoom lifecycle cutover must also be quiesced: drain old workers and old
`merge_window` tasks before enabling UUID-bound lifecycle handling. New tasks
carry only an internal encrypted-inbox delivery ID; that row owns the occurrence
and generation fences. Legacy tasks carry only meeting ID/generation and are
deliberately rejected once UUID-bound state exists. UUID-less participant/end
callbacks cannot mutate a UUID-bound occurrence. A session created entirely by
an old revision can finish through the narrow legacy fallback, but mixed old/new
callbacks must be allowed to drain rather than manually reassigned.

Stop, leave, and merge-window publication now write an encrypted durable
teardown intent first. Provider timeouts, worker loss, and broker publication
failure therefore remain visible in webhook-inbox health and are retried by
the existing reconciler. An unresolved intent is an operational alert: verify
the pseudonymous log references and let reconciliation confirm bot release
before starting another occurrence on the same PMR.

The server-side `ZoomMeeting.appointment_id` remains intact for schedule and
note navigation. A separate occurrence digest, expiry, and consumed timestamp
form the one-use launch authorization; consumption is serialized with a
database row lock before the Recall bot is created. A provider timeout or
failed launch therefore requires an operator to verify Recall state and a
trusted workflow to deliberately re-authorize before retry. This reduces
automatic recovery, but prevents a Redis reset or replayed join event from
starting a second recording against the same chart.

## Scenario 6 — Doctor leaves, patient also leaves, doctor comes back alone

Edge case: doctor returns to an empty room. Should not resume.

**Steps:**
1. Doctor + patient in the meeting, recording active.
2. Doctor leaves (pause).
3. Patient leaves a moment later.
4. Doctor comes back, **alone**, within 10 min.
5. Wait — see what happens. (Then end the meeting to clean up.)

**Expected:**
- No `start_recording` call when the doctor returns alone (no patient to record).
- Merge window task **remains armed** — the visit is still effectively over.
- If 10 min elapse with no patient joining, the bot disconnects normally.

**How to verify:**
- Logs: no `recording resumed` after the doctor rejoins.
- Redis: `merge_task_id` still set after the doctor's rejoin event.

## Scenario 7 — Repeated return attempts remain blocked

Stress the one-segment fence.

**Steps:**
1. Doctor + patient, recording active.
2. Doctor leaves → wait 30s → doctor returns.
3. Patient leaves and rejoins before the release task fires.
4. End the meeting.

**Expected:**
- Exactly one Start Recording operation occurred.
- No return event revokes the release task or produces another recording.
- The appointment receives exactly one transcript.

**How to verify:**
- Logs contain one or more `recording continuation blocked without exact encounter binding` events and no `recording resumed` event.

## Scenario 8 — Family member joins (no role change)

Sanity check that secondary participants don't break anything.

**Steps:**
1. Doctor + patient in the meeting, recording active.
2. A family member (third participant) joins.
3. Family member leaves.
4. Patient leaves.
5. Doctor ends the meeting.

**Expected:**
- Recording stays active during the whole period the doctor + at least one non-doctor are present.
- When the family member leaves but the patient remains, no pause happens.
- When the patient leaves, pause arms (Scenario 4 behavior).

**How to verify:**
- Logs: only one pause event (triggered by the patient leaving), not by the family member.

## Scenario 9 — OAuth revoked between sessions

Already shipped, but worth a sanity check.

**Steps:**
1. Doctor revokes Zoom OAuth in their Zoom account settings.
2. Doctor starts a meeting and tries to use Cosmaneura.

**Expected:**
- A banner + toast on the next page load tells the doctor the integration was revoked.
- No bot is created; no errors leak.

**How to verify:**
- Logs: `cannot start appointment: doctor revoked Zoom OAuth`.
- App UI: the revoked banner is visible.

## Scenario 10 — Short meeting and delayed PMR events

**Steps:**
1. Start a new PMR occurrence and end it within 60 seconds.
2. Confirm teardown completes despite the legacy `starting_lock` still being
   present.
3. Start another occurrence using the same PMR meeting ID.
4. Redeliver a captured participant/ended payload from the first occurrence.

**Expected:**
- The matching short-meeting end stops/leaves the bot and clears the session.
- Delayed callbacks from the first occurrence do not change participant
  counts, stop recording, clear state, or alter the second occurrence.
- Logs report an occurrence fence without exposing the UUID.

## After all scenarios — sanity sweep

- [ ] Tail the staging logs for the test window. There should be **zero** unhandled exceptions, especially from `merge_window.perform`, `_pause_and_arm_merge_window`, `_resume_or_start_recording`, and `summarize.perform`.
- [ ] Spot-check Celery: any stuck `merge_window_expire` tasks in the queue? They should fire 10 min after they're queued and then disappear.
- [ ] DB: no orphaned `ai_visit_transcript` rows with NULL `duration` or empty `transcript_data`.
- [ ] DB: every accepted Recall transcript has all three exact
  `recall:recording:{recording_id}:doctor_id|appointment_id|patient_id`
  bindings; bot-scoped keys are not accepted as fallback authority.
- [ ] DB: the captured `zoom_meeting` keeps `appointment_id`, has a matching
  occurrence digest and expiry, and has a non-null authorization-consumed time.
- [ ] Redis: duplicate or out-of-order participant callbacks do not inflate
  presence counts, and membership set/hash fields contain no raw Zoom IDs.

## If something fails

1. Capture the meeting_id and approximate timestamps.
2. In an approved secure environment, inspect only the minimum Redis key
   presence needed; do not paste raw provider or patient identifiers into a
   ticket.
3. Search structured logs by the system-generated HMAC reference.
4. Open an issue / Slack thread with the scenario number, expected vs. actual
   result, bounded state, and sanitized log metadata.

Don't merge to master until all scenarios pass.
