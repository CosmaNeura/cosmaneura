# Zoom Flow — Manual Testing Guide (Phase 1)

Step-by-step E2E checks to run on **staging** before merging `feat/zoom-merge-window` to master. Goal: prove the pause/resume + merge window behavior works end-to-end against real Zoom, real Recall, and the real database.

Each scenario lists: **Setup → Steps → Expected → How to verify**. If any expected step doesn't hold, capture logs + Redis snapshot and flag.

## Prerequisites

- Staging is deployed with the `feat/zoom-merge-window` branch.
- You have a doctor account on staging with Zoom OAuth connected.
- You have a second Zoom account (or a guest browser) to act as the patient.
- A terminal with `redis-cli` access to the staging Redis (or the equivalent admin tool you use).
- Tail of the API logs in one window (`heroku logs --tail` / equivalent), filtered for `zoom_webhooks`, `merge_window`, `recall`, `summarize`.

### Useful Redis commands

```bash
# Replace MEETING_ID with the actual Zoom meeting id
redis-cli KEYS "recall:meeting:MEETING_ID:*"
redis-cli GET "recall:meeting:MEETING_ID:bot_id"
redis-cli GET "recall:meeting:MEETING_ID:recording_active"
redis-cli GET "recall:meeting:MEETING_ID:merge_task_id"
redis-cli GET "recall:meeting:MEETING_ID:paused_at"
```

Key meanings:
- `bot_id` set → a Recall bot is tracked for this meeting (may be paused).
- `recording_active = 1` → recording is currently running.
- `recording_active` absent + `bot_id` present → paused inside the merge window.
- `merge_task_id` present → a 10-min finalize task is armed.

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
- One AI visit note generated for the appointment.

**How to verify:**
- Logs: `appointment session started` (once) and `meeting ended with active recording`.
- App UI: the appointment shows a note with the conversation summarized.
- DB: `SELECT COUNT(*) FROM ai_visit_transcript WHERE appointment_id = '...'` returns 1.

## Scenario 2 — Doctor steps out briefly (<10 min), patient stays

This is the core pause/resume.

**Steps:**
1. Doctor starts the meeting, patient joins. Recording starts.
2. Speak for ~30s so we have content.
3. Doctor leaves Zoom (close the tab or hang up). Patient stays in the room.
4. Wait 60–120 s.
5. Doctor rejoins.
6. Speak for another ~30s.
7. Doctor ends the meeting.

**Expected:**
- Bot does **not** leave Zoom while the doctor is out (you can see it still listed as a participant in the meeting).
- When the doctor returns, recording resumes on the same bot — no new bot created.
- The final note **merges both halves** into one document.

**How to verify:**
- Logs (in order): `participant left: doctor`, `recording paused, merge window armed reason=doctor_left`, then on return `recording resumed on existing bot within merge window`.
- Redis right after doctor leaves: `bot_id` present, `recording_active` absent, `merge_task_id` present, `paused_at` present.
- Redis right after doctor returns: `recording_active = 1`, `merge_task_id` absent.
- DB: one bot id was used for both segments (`SELECT recording_id FROM ai_visit_transcript WHERE appointment_id = '...'` should return 2 rows).
- The merged note's transcript view (or `transcript_data` field) contains a `[Segment 2 starts]` marker between the two halves.

## Scenario 3 — Doctor steps out for too long (>10 min)

The merge window has to actually expire and clean up.

**Steps:**
1. Doctor + patient in the meeting, recording active.
2. Doctor leaves. Patient stays (or also leaves — either works).
3. Wait **>10 minutes** without anyone rejoining.

**Expected:**
- After ~10 min, the bot disconnects from Zoom automatically.
- A note is generated from whatever was recorded before the doctor left.
- If a new patient joins after this point with the same meeting_id, the band-aid filter (scenario 7) kicks in and a fresh appointment is created.

**How to verify:**
- Logs: `merge window expired, releasing bot`.
- Redis after expiry: all `recall:meeting:{id}:*` keys cleared (or at least `bot_id`, `merge_task_id`, `paused_at` gone).
- App UI: a note is present for the appointment.

## Scenario 4 — Last patient leaves while doctor stays

Mirrors Scenario 2 but the trigger is the patient leaving, not the doctor.

**Steps:**
1. Doctor + patient #1 in the meeting.
2. Patient #1 leaves. Doctor stays.
3. Within 5 minutes, patient #2 joins.
4. Doctor finishes the visit and ends the meeting.

**Expected (Mode A back-to-back inside window):**
- Bot stays connected after patient #1 leaves.
- When patient #2 joins, recording resumes on the **same bot**.
- One merged note covers the visit.

> ⚠️ Phase 1 v1 limitation: both patients land on the same appointment. Patient identity for #2 comes from the original calendar binding / display name. This is acceptable for v1 — Phase 2 fixes it via per-patient calendar binding.

**How to verify:**
- Logs: `recording paused, merge window armed reason=last_patient_left`, then `recording resumed on existing bot within merge window`.
- `mock_recall["create_bot"]` equivalent: only one bot created in the meeting's lifetime.

## Scenario 5 — Back-to-back patients across the window (>10 min gap)

This is what the matcher band-aid (task 1.5) was added for.

**Steps:**
1. Run Scenario 2 or 4 to completion — finish with a clean note generated for appointment A.
2. Wait >10 min for the merge window to expire and the bot to leave.
3. Start a **new** Zoom meeting with the same meeting_id (PMR) or the same doctor.
4. A different patient joins.

**Expected:**
- A new bot is created (not the old one — it disconnected after the window).
- A **new appointment** is created for the new patient; the old appointment is not touched.
- A separate note is generated for the new appointment.

**How to verify:**
- Logs: a fresh `appointment session started` with a different `appointment_id` than the previous run.
- DB: the old appointment still has exactly one transcript and one note; a brand-new appointment row exists for the new patient.

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

## Scenario 7 — Doctor steps out twice in the same visit

Stress the revoke + re-arm logic.

**Steps:**
1. Doctor + patient, recording active.
2. Doctor leaves → wait 30s → doctor returns (resume #1).
3. Doctor leaves again → wait 30s → doctor returns (resume #2).
4. End the meeting.

**Expected:**
- Two pause events and two resumes. One bot. One final note merging all segments.
- Each pause arms a fresh `merge_task_id`; each return revokes the previous one cleanly.

**How to verify:**
- Logs: two `recording paused, merge window armed` + two `merge window task revoked` + two `recording resumed`.

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

## After all scenarios — sanity sweep

- [ ] Tail the staging logs for the test window. There should be **zero** unhandled exceptions, especially from `merge_window.perform`, `_pause_and_arm_merge_window`, `_resume_or_start_recording`, and `summarize.perform`.
- [ ] Spot-check Celery: any stuck `merge_window_expire` tasks in the queue? They should fire 10 min after they're queued and then disappear.
- [ ] DB: no orphaned `ai_visit_transcript` rows with NULL `duration` or empty `transcript_data`.
- [ ] Notes UI: the rendered transcript view for the merged-segment scenarios shows the `[Segment N starts]` markers in the right places.

## If something fails

1. Capture the meeting_id and approximate timestamps.
2. Snapshot Redis: `redis-cli KEYS "recall:meeting:MEETING_ID:*"` and `GET` each key.
3. Grep logs for the meeting_id across the test window.
4. Open an issue / Slack thread with: scenario number, what happened vs. what was expected, the Redis snapshot, and the relevant log slice.

Don't merge to master until all scenarios pass.
