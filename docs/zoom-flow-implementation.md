# Zoom Meeting Flow — Implementation Plan

Working doc. Update day by day as tasks land. Keep the "Daily log" at the bottom honest — it's the only thing that lets you pick this up cold after a weekend.

## Context

We support two clinician modes:

- **Mode A — Persistent Room.** Doctor lives in a single PMR; patients dial in/out all day on the same `meeting_id`.
- **Mode B — Per-Meeting.** One Zoom meeting per patient, scheduled or ad-hoc.

The current Redis state machine works for the happy path but has known gaps around back-to-back patients (Mode A), patient identity inference, and silent failures (revoked OAuth, never-admitted bot). This plan closes those gaps in three phases.

The architecture stays as today: Zoom OAuth + Webhooks + ZAK-authenticated Recall.ai bot. No Meeting SDK, no embedded UI surface.

## Locked decisions (do not relitigate)

### Four defaults

1. **Merge window: 10 min.** Two recording segments tied to the same appointment within 10 min merge into one note. Beyond → previous note finalized, next is a separate event.
2. **Ad-hoc admit timeout: 10 min.** Bot waits in waiting room. If never admitted, leaves silently. Surfaced in app via the Needs-Assignment inbox + email.
3. **Scheduled = ZAK auto-admit, ad-hoc = manual admit.** A calendar entry is the consent moment. Without one, the doctor must explicitly admit.
4. **Needs-Assignment inbox.** Recordings without a matched appointment land here with an AI-suggested patient name from the first 60–90s of the transcript. Doctor confirms or reassigns. Suggestion never auto-assigns.

### Five implementation clarifications (confirmed with leadership)

1. **N transcripts per appointment**, summarize concatenates at generation. Don't mutate written `AIVisitTranscript` rows.
2. **Pause, don't leave.** On `participant_left:doctor` call `stop_recording` only and keep the bot connected. Issue `leave_call` only after the 10-min merge window expires without resume.
3. **Calendar tie-break:** within a single source, pick `min |entry.start − meeting.started|`; on tie, pick the earlier entry. Acuity wins over Google Calendar across sources.
4. **"Didn't admit" → Needs-Assignment inbox** + email to doctor. In-Zoom alert is nice-to-have (likely impossible from waiting room); skip unless trivially cheap.
5. **Practice scoping = default-private with explicit share.** A doctor's notes are private by default; the practice account model lets them share with covering colleagues opt-in. Not strict isolation.

### Dropped / deferred

- In-app "next patient" handoff UI — calendar binding makes it unnecessary.
- Mode A back-to-back special-case logic — same.
- Family-member tagging (separate from patient) — diarization handles voice separation but not role assignment. Acceptable v1 limitation. Flagged.
- Configurable per-doctor grace period — irrelevant under "stop on leave + merge window."

## Phase 1 — this week

Behavioral changes inside the existing webhook + Recall plumbing. No new integrations, no migrations beyond optional indices.

### Tasks

- [ ] **1.1 Replace `recording_guard` with merge-window logic.**
  - Delete `app/jobs/recording_guard.py` and its enqueue point in `app/services/zoom_webhooks.py:406-410`.
  - On `participant_left:doctor` while `recording_active=true`: call `recall.stop_recording(bot_id)` only. Keep `bot_id` in Redis. Set `recording_active=false`. Stamp `recall:meeting:{meeting_id}:paused_at = now`.
  - New Celery task `merge_window_expired(meeting_id)` armed with countdown 600s (10 min). Stores its task id under `recall:meeting:{meeting_id}:merge_task_id`.
  - When the task fires, double-check `recording_active=false` (no resume happened); if so call `recall.leave_call(bot_id)` and run `_clear_meeting_keys`. If recording resumed first, no-op.

- [ ] **1.2 Resume on next valid trigger.**
  - In `handle_participant_joined`, after the existing trigger predicate (doctor present + non-doctor present + `recording_active=false`), check if `bot_id` is still set in Redis. If yes, call `recall.start_recording(bot_id)` instead of `recall.create_bot(...)`. Revoke `merge_task_id` if present.
  - The same predicate must apply when the doctor returns: doctor's `participant_joined` should resume recording if a non-doctor is in the room and `bot_id` is set.
  - The bot does **not** re-trigger Zoom's recording-consent prompt on resume because the bot is the same session.

- [ ] **1.3 N transcripts per appointment.**
  - Verify `AIVisitTranscript.appointment_id` already supports multiple rows per appointment (no unique constraint). If a unique index exists, drop it via Alembic.
  - Each `start_recording` produces a distinct `recording_id` → distinct `AIVisitTranscript` row. Existing code path likely already does this; confirm by reading the recall webhook handler that creates the transcript row.

- [ ] **1.4 `summarize.py` reads all transcripts of the appointment.**
  - Change `perform(transcript_id, schema_id)` → either accept `appointment_id` directly, or look up the appointment from the trigger transcript and load **all** transcripts for that appointment ordered by `created_at`.
  - Concatenate `transcript_data` lists into a single ordered list (preserve segment boundaries with a marker like `{"speaker": "system", "text": f"[Segment {i+1} starts]"}` so the LLM can reason about gaps if needed).
  - The existing "skip if note exists for (transcript_id, schema_id)" guard becomes "skip if note exists for (appointment_id, schema_id)". Make sure regeneration after a new segment lands replaces or supersedes the prior note (decision: **replace**, since the new segment is part of the same visit).

- [ ] **1.5 Band-aid for scenario #9 (appointment matcher).**
  - In `_start_appointment_session` (`app/services/zoom_webhooks.py:188-203`), filter the appointment query to exclude any appointment that already has a transcript:
    ```python
    .outerjoin(AIVisitTranscript, AIVisitTranscript.appointment_id == Appointment.id)
    .where(AIVisitTranscript.id.is_(None))
    ```
  - This is a temporary safeguard. Phase 2 replaces the time-window matcher with calendar binding entirely.

- [ ] **1.6 Tests.**
  - Update `tests/services/zoom_webhooks_test.py` for the new pause/resume flow.
  - Add a test for the merge-window expiry path.
  - Add a test that `summarize` correctly merges two transcripts on the same appointment.
  - Add a test for the appointment-matcher filter (no transcript → matched, with transcript → skipped).

- [ ] **1.7 Manual end-to-end verification.**
  - Open a Zoom meeting, doctor joins, patient joins, recording starts.
  - Doctor leaves. Confirm bot stays in meeting, recording stops.
  - Doctor returns within 60s. Confirm `start_recording` resumes on the same bot.
  - Doctor leaves again. Wait 10+ min. Confirm bot leaves and merge-window task fires.
  - Verify the resulting note merges both segments.

### Phase 1 done when

- Manual scenario above passes end-to-end on staging.
- All affected tests pass in CI.
- A doctor stepping out for <10 min produces one note covering both halves; >10 min produces two notes.
- Mode A back-to-back doesn't pile transcripts onto a stale appointment.

## Phase 2 — next sprint

Calendar binding (Acuity + Google Calendar) and ad-hoc waiting-room flow. Replaces the time-window matcher entirely.

### Tasks

- [ ] **2.1 Acuity OAuth + sync.** Per-doctor connection, fetch upcoming appointments. Store in a new `external_calendar_event` table (or extend `Appointment`) with `source = 'acuity' | 'google'`, `external_id`, `start_at`, `end_at`, `patient_label`.
- [ ] **2.2 Google Calendar OAuth + sync.** Same shape as Acuity.
- [ ] **2.3 Calendar-driven appointment binding.** On `meeting.started`, query for events in `[start − 30 min, start + 30 min]` for the doctor. Apply the tie-break rule (Acuity wins; within source, min absolute delta, ties earlier). Bind the resulting appointment.
- [ ] **2.4 Ad-hoc waiting-room flow.** When no calendar match: bot is created without `zak_url` so it joins the waiting room. Recall's `automatic_leave.waiting_room_timeout = 600` enforces the 10-min cap.
- [ ] **2.5 Needs-Assignment inbox.** New table `unassigned_recording`. Recordings that finish without a bound appointment land here. AI infers a patient name suggestion from the first 60–90s of transcript (kept as suggestion, not assignment). New `/notes/inbox` route in the API and a new sidebar entry in the frontend.
- [ ] **2.6 Drop the time-window matcher.** Once 2.3 ships, remove the band-aid from 1.5 and the original ±4h logic in `_start_appointment_session`.
- [ ] **2.7 "Didn't admit" notification.** Email + Needs-Assignment entry when waiting-room timeout fires.

### Phase 2 done when

- A scheduled meeting binds to its calendar entry automatically; patient identity comes from the calendar.
- An ad-hoc meeting either gets admitted (records normally + lands in inbox for assignment) or times out (logged + emailed).
- The ±4h matcher is gone.

## Phase 3 — before first multi-clinician customer

Practice account schema. Do not bundle with Phase 2.

### Tasks

- [ ] **3.1 `Practice` table.** A practice has many doctors. Doctors keep their existing rows; add `practice_id` FK.
- [ ] **3.2 Default-private scoping.** All notes/appointments/transcripts query-filtered by `doctor_id` by default.
- [ ] **3.3 Explicit share grants.** New table `note_share_grant` (doctor_id_grantor, doctor_id_grantee, scope, expires_at). Used for covering colleagues. UI is a per-note "share" button.
- [ ] **3.4 Practice-level admin.** A practice admin can list its doctors, but cannot read notes unless explicitly granted.
- [ ] **3.5 Migration plan.** Existing solo doctors get an auto-created practice with themselves as the single member.

## Layered later (no-regrets)

- AI transcript-name inference as a verification check on the doctor's existing patient assignment, flagging soft mismatches in the note review UI.
- Doctor-not-joined telemetry on the dashboard (counts of meetings where the bot was created but no recording happened).
- Per-segment timestamps in the merged note so the doctor can see "Patient stepped out 02:14 → 03:01" reflected in the transcript view.

## Edge case reference

Use this as the lookup table when something weird happens in production. Cross-reference with `app/services/zoom_webhooks.py`.

| # | Scenario | Behavior after Phase 1 |
|---|---|---|
| 1 | Doctor first, patient second | Recording starts on patient join. |
| 2 | Patient first, doctor second | Patient queued; recording starts on doctor join. |
| 3 | Family member joins before patient | Patient identity = family member's display name (Phase 2 fixes via calendar). |
| 4 | Family member joins during active session | Logged as additional participant; recording continues; transcript captures both. |
| 5 | Doctor leaves <10 min and returns | Pause → resume on same bot. Single merged note. |
| 6 | Doctor leaves >10 min | Bot leaves; current note finalized. New activity is a separate event. |
| 7 | Last non-doctor leaves; doctor stays | Recording stops, bot stays connected during merge window. Next non-doctor join triggers resume on same bot if within window. |
| 8 | Patient leaves; family stays | Same as 7 — counter at 0 stops recording. |
| 9 | Mode A back-to-back patients | Phase 1 band-aid: matcher skips appointments with transcripts. Phase 2: calendar binding fully fixes. |
| 10 | Patient #2 joins before #1 leaves | Both in same recording. Mode A v1 limitation; future work to split by speaker. |
| 11 | Late patient after recording closed | New session opens. Same as 9. |
| 12 | Doctor never joins | No recording. After Phase 2, "didn't admit" enters inbox + email. |
| 13 | Doctor host_id ≠ DB provider_user_id | Currently misclassifies doctor as "other." Resolved by practice-account scoping in Phase 3 (one Clerk = one doctor row, fewer hosts collide). |
| 14 | Two clinicians share Cosmaneura login | Currently co-mingles PHI. Phase 3 practice schema removes the need to share logins. |
| 17 | OAuth revoked between sessions | Already shipped: integration row deleted, banner + toast on next page load (commits `d6ccd21`, `fd2bdf7`). |

## Daily log

Append a one-line entry per working day. What you finished, what you blocked on, what you'll pick up tomorrow.

- **2026-05-13** — Phase 1.1+1.2 landed on `feat/zoom-merge-window`: replaced `recording_guard` with `merge_window` (600s), bot stays connected during pause, doctor/patient return inside the window resumes via `start_recording` on the same bot. Tests updated and green.
- **2026-05-14** — Phase 1.3–1.6 landed on the same branch. 1.3 verified (no unique constraint on `ai_visit_transcript.appointment_id`, no migration needed). 1.4: `summarize.perform` now concatenates all transcripts for an appointment with `[Segment N starts]` markers, sums durations, replaces stale notes, and skips when the note already reflects the latest transcript. 1.5: matcher in `_start_appointment_session` excludes appointments that already have a transcript (band-aid for Mode A scenario #9). Suite green: 85 passed across services/job/routes. **Next:** 1.7 manual E2E on staging.

### 1.7 Manual E2E checklist (staging)

Run end-to-end with a real Zoom + Recall + database. Sign off before merging to master.

- [ ] Doctor + patient join → recording starts (single `create_bot`, single `start_recording`).
- [ ] Doctor leaves with patient still present → `stop_recording` fires, bot stays in meeting, merge_task_id set in Redis, no `leave_call`.
- [ ] Doctor returns within 60s → `start_recording` issued on the SAME bot, no new `create_bot`, merge task revoked.
- [ ] Doctor leaves a second time, this time stays out 10+ min → `merge_window.perform` fires, `leave_call` issued, bot disconnects.
- [ ] Last patient leaves while doctor stays → same pause behavior (stop_recording + arm merge task, bot connected).
- [ ] Patient B joins within 10 min of patient A leaving → resume on same bot, single appointment carries both segments.
- [ ] After resume + meeting end, the resulting note merges both segments (look for `[Segment 2 starts]` in the rendered transcript view or the `transcript_data` in the DB).
- [ ] After the merge window expires and a fresh meeting starts with a different patient → matcher creates a brand-new appointment instead of binding to the prior one (verify via `recall:bot:{bot_id}:appointment_id` in Redis).
- [ ] Tail logs for any unexpected exceptions in `merge_window`, `_pause_and_arm_merge_window`, `_resume_or_start_recording`.
