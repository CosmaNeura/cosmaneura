# Doctor Presence Guard for Zoom Recording

**Date:** 2026-04-24
**Status:** Design approved, pending implementation plan
**Context:** Incident on 2026-04-24T00:56Z where a PMR triggered `meeting.started` without the doctor actually being present. Cosma recorded the participant and a clinical note was auto-generated. Root cause: the recording pipeline never verified the doctor's presence; any non-doctor, non-bot participant triggers the flow.

## Goal

The bot must accompany the doctor. Recording only happens when the doctor is confirmed present in the Zoom call. If the doctor leaves, recording stops after a grace period. If the doctor never joins, no bot is ever created, no patient record is created, no clinical note is generated.

As a secondary goal, the PMR model (doctor leaves their personal meeting room open all day, patients come and go) must produce **one appointment per patient** rather than one appointment per Zoom session. When a patient leaves and a new one enters, a new appointment begins.

## Non-goals

- UI changes (appointment roster, participant list, etc.). Logging-only visibility for now.
- Real-time alerts when suspicious participant patterns happen.
- Automatic detection of "accompanying family vs. new patient". The rule is purely temporal (see State model).
- Merging multiple recordings into a single transcript/note.

## Identity signals

**Doctor identity** — match `participant.id` from the Zoom `meeting.participant_joined` payload against `DoctorVideoIntegration.provider_user_id` (same Zoom user ID we already match on `meeting.started.host_id`). This is tamper-resistant: `participant.id` is only populated when the participant is authenticated in Zoom as that user, so a guest setting `user_name = "Dev Cosmaneura"` cannot spoof it.

**Bot identity** — keep the current `user_name == "Cosma"` match. We control the bot name at create time, and the bot joins as a guest without a `participant.id`.

## State model

Redis keys under prefix `recall:meeting:{meeting_id}:` with 24h TTL (matches existing convention):

| Key | Value | Set when | Deleted when |
|---|---|---|---|
| `doctor_id` | doctor ULID | meeting.started | meeting.ended / stale clear |
| `host_id` | Zoom user_id of the doctor (new) | meeting.started | meeting.ended / stale clear |
| `doctor_present` | "1" (new) | doctor participant joins | doctor participant leaves |
| `bot_id` | Recall bot id (existing, now deferred) | first non-doctor enters while doctor is present | appointment closes (count→0 or grace expires or meeting.ended) |
| `recording_active` | "1" (new) | `start_recording` returns a recording_id | `stop_recording` or grace expires |
| `participants_order` | Redis LIST of `participant_id`, FIFO via RPUSH (new) | non-doctor joins | non-doctor leaves (LREM) / appointment closes |
| `participants_names` | Redis HASH `{participant_id: user_name}` (new) | non-doctor joins | non-doctor leaves (HDEL) / appointment closes |
| `patient_count` | int (existing) | incremented on non-doctor join | decremented on non-doctor leave |
| `starting_lock` | "1" (existing) | meeting.started | 60s TTL or meeting.ended |
| `grace_task_id` | Celery task id (new) | doctor leaves while recording_active | doctor returns / task executes / meeting.ended |

Remove `doctor_name` key — no longer used. Participant-matching uses `participant.id` exclusively.

## Handler changes (`app/services/zoom_webhooks.py`)

### `handle_meeting_started`

- Still resolves doctor by `host_id`.
- Still acquires `starting_lock` and clears stale meeting keys.
- Still marks `ZoomMeeting.started`.
- **Removes** `recall.create_bot` and storage of `bot_id`.
- **Removes** storage of `doctor_name`.
- **Adds** storage of `host_id` (the Zoom `provider_user_id`) so `handle_participant_joined` can match participants without a DB lookup.

Log: `meeting session started, awaiting doctor`.

### `handle_participant_joined`

```
participant_id = payload.participant.id
user_name = payload.participant.user_name

# bot (matched by controlled name)
if user_name == "Cosma":
    log participant event (role="bot", event="joined")
    return

# doctor (matched by Zoom user id)
if participant_id == host_id:
    set doctor_present = "1"
    if grace_task_id exists:
        celery_app.control.revoke(grace_task_id)
        delete grace_task_id
        log "doctor returned within grace, task revoked"
    if LLEN(participants_order) > 0 and not recording_active:
        first_id = LINDEX(participants_order, 0)
        first_name = HGET(participants_names, first_id)
        _start_appointment_session(meeting_id, {"id": first_id, "user_name": first_name})
    log participant event (role="doctor", event="joined")
    return

# other
rpush participants_order, participant_id
hset participants_names[participant_id] = user_name
incr patient_count
if not doctor_present:
    log "non-doctor joined before doctor, holding"
    return
if not recording_active:
    _start_appointment_session(meeting_id, this_participant)
else:
    log "additional participant in active session"
```

### `handle_participant_left`

```
participant_id = payload.participant.id
user_name = payload.participant.user_name

if user_name == "Cosma":
    return

# doctor
if participant_id == host_id:
    delete doctor_present
    if recording_active:
        task = stop_recording_if_absent.apply_async(
            args=[meeting_id], countdown=90
        )
        set grace_task_id = task.id
        log "doctor left, grace period armed"
    return

# other
lrem participants_order, 0, participant_id
hdel participants_names, participant_id
new_count = decr patient_count
if new_count < 0:
    reset to 0 (existing safety)
if new_count == 0 and doctor_present and recording_active:
    recall.stop_recording(bot_id)
    recall.leave_call(bot_id)                   # bot destruction; new helper
    delete bot_id, recording_active, participants_order, participants_names
    log "appointment session closed"
```

### `handle_meeting_ended`

- Revoke `grace_task_id` if present.
- If `bot_id` present → `recall.stop_recording(bot_id)` + `recall.leave_call(bot_id)`.
- If `bot_id` absent → log `meeting ended without recording` (this is the expected outcome when the doctor never joined; correct behavior).
- Clear all meeting keys (existing `_clear_meeting_keys` extended with new keys).

## Appointment lifecycle

One bot = one appointment. Extracted helper:

```python
def _start_appointment_session(meeting_id: str, participant: dict) -> None:
    """Idempotent via recording_active. Creates bot, waits for ready,
    starts recording, resolves/creates appointment + patient."""
    if redis.get(f"recall:meeting:{meeting_id}:recording_active"):
        return

    doctor_id = _rget(redis, f"recall:meeting:{meeting_id}:doctor_id")

    # Resolve doctor + integration (needed for zak token)
    doctor = Doctor.get(id=doctor_id)
    zak_token = zoom_service.get_zak_token(doctor)
    meeting_url = zoom_service.get_meeting_join_url(doctor, meeting_id)

    bot_data = recall.create_bot(
        meeting_url=meeting_url,
        zak_token=zak_token,
        manual_recording=True,
        metadata={"doctor_id": doctor_id, "meeting_id": meeting_id},
    )
    bot_id = bot_data["id"]
    redis.set(f"recall:meeting:{meeting_id}:bot_id", bot_id, ex=ttl)

    recording_data = recall.start_recording(bot_id)  # waits for ready
    recording_id = recording_data.get("id")
    if not recording_id:
        log "bot never reached ready state, aborting appointment"
        recall.leave_call(bot_id)
        delete bot_id
        return

    redis.set(f"recall:meeting:{meeting_id}:recording_active", "1", ex=ttl)

    # Appointment: prefer pre-existing (scheduled in last 4h / next 1h) else ad-hoc
    appointment = _find_or_create_appointment(
        doctor_id=doctor_id,
        patient_name=participant["user_name"] or "Unknown",
    )

    # Link recording metadata (existing pattern)
    redis.set(f"recall:recording:{recording_id}:doctor_id", doctor_id, ex=ttl)
    redis.set(f"recall:recording:{recording_id}:appointment_id", str(appointment.id), ex=ttl)
    redis.set(f"recall:recording:{recording_id}:patient_id", str(appointment.patient_id), ex=ttl)
    redis.set(f"recall:bot:{bot_id}:appointment_id", str(appointment.id), ex=ttl)
    redis.set(f"recall:bot:{bot_id}:patient_id", str(appointment.patient_id), ex=ttl)

    log "appointment session started"
```

## Grace period for doctor absence

**New Celery task** in `app/jobs/recording_guard.py`:

```python
@celery_app.task
def stop_recording_if_absent(meeting_id: str) -> None:
    redis = get_redis()
    if redis.get(f"recall:meeting:{meeting_id}:doctor_present"):
        log "grace check: doctor back in call, no action"
        return

    bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")
    if not bot_id or not redis.get(f"recall:meeting:{meeting_id}:recording_active"):
        return

    log "grace period expired, stopping recording"
    recall.stop_recording(bot_id)
    recall.leave_call(bot_id)
    redis.delete(f"recall:meeting:{meeting_id}:recording_active")
    redis.delete(f"recall:meeting:{meeting_id}:bot_id")
    redis.delete(f"recall:meeting:{meeting_id}:grace_task_id")
    redis.delete(f"recall:meeting:{meeting_id}:participants_order")
    redis.delete(f"recall:meeting:{meeting_id}:participants_names")
```

**Decision: if grace expires and the doctor returns later, do not resume recording.** That appointment is over. The next non-doctor entering starts a fresh appointment.

**Robustness:**
- The task's first check of `doctor_present` makes `revoke` an optimization, not a correctness requirement. Even if revoke fails, the task aborts safely when the doctor is back.
- Worker restart mid-countdown: Celery ETA tasks survive restarts when using Redis/RabbitMQ as broker (current setup uses Redis).
- Multiple leaves in 90s: each leave revokes the prior `grace_task_id` before scheduling a new one. Only one pending task at a time.

## New Recall helper

Add to `app/services/recall.py`:

```python
def leave_call(bot_id: str) -> None:
    """Tell the bot to leave the meeting. Triggers the 'done' terminal
    status and stops Recall billing for this bot."""
    with httpx.Client() as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/{bot_id}/leave_call/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
    if response.status_code == httpx.codes.BAD_REQUEST:
        log.info("recall leave_call skipped, bot already done", bot_id=bot_id)
        return
    if not response.is_success:
        log.error("recall leave_call error", status=response.status_code, body=response.text)
    response.raise_for_status()
    log.info("recall bot instructed to leave call", bot_id=bot_id)
```

Also extend `create_bot` signature to accept `metadata: dict | None = None` and forward it to Recall so every bot is tagged with `doctor_id` and `meeting_id` for usage-based billing reconciliation.

Safety net: consider setting `automatic_leave.waiting_room_timeout` and `automatic_leave.in_call_not_recording_timeout` in the create_bot payload so Recall auto-destroys a bot if our `leave_call` call is lost. Concrete value to be decided at implementation time.

## Participant-event logging

Every `participant.joined` / `participant.left` event logs a structured record:

```python
log.info(
    "participant event",
    event="joined" | "left",
    meeting_id=...,
    participant_id=...,           # empty for guests
    user_name=...,
    role="doctor" | "bot" | "other",
    doctor_present=bool,          # state after this event
    recording_active=bool,
    patient_count=int,
    bot_id=...,                   # if exists
    appointment_id=...,           # if exists
)
```

### State-transition log events (distinct, queryable names)

- `meeting session started, awaiting doctor`
- `doctor joined, no participants waiting`
- `doctor joined, creating bot for waiting participant`
- `non-doctor joined before doctor, holding`
- `appointment session started`
- `additional participant in active session`
- `appointment session closed` (patient_count → 0 with doctor present)
- `doctor left, grace period armed`
- `doctor returned within grace, task revoked`
- `grace period expired, stopping recording`
- `meeting ended without recording`
- `meeting ended with active recording`

### Investigator KQL template

Shipped in `infra/Justfile` as a new recipe `azure_logs_for_meeting meeting_id=...`:

```kusto
union *
| where TimeGenerated > ago(24h)
| where ContainerAppName_s in ('cosmaneura-prod-app', 'cosmaneura-prod-worker')
| where Log_meeting_id_s == "<meeting_id>"
| project TimeGenerated, Log_event_s, Log_role_s, Log_user_name_s,
          Log_doctor_present_b, Log_recording_active_b, Log_patient_count_d,
          Log_bot_id_g, Log_appointment_id_s
| order by TimeGenerated asc
```

## Testing strategy

Unit tests in `tests/app/services/test_zoom_webhooks.py` (new or extended). Use `fakeredis` and monkeypatch `recall.*` calls. Each test asserts Redis state transitions + which Recall methods were called with which args.

Happy paths:
1. `meeting.started` → no bot created, keys set correctly
2. Doctor joins first, no one else → no bot, no recording, `doctor_present=1`
3. Doctor joins first, then patient → bot created, recording started, appointment created
4. Patient joins first, doctor joins second → bot created when doctor arrives, recording starts immediately
5. Two patients sequentially in same session → two appointments, two bots, two `start_recording` calls
6. Patient + accompanying family (count 1→2→1) → single appointment, no extra bot
7. Doctor leaves, returns in <90s → grace task revoked, recording continues
8. Doctor leaves, does not return → grace expires, `stop_recording` + `leave_call` called
9. `meeting.ended` without doctor ever present → no bot, no recording, no appointment
10. `meeting.ended` during active recording → stop + leave_call

Edge cases:
11. Duplicate `meeting.started` within lock window → single handler run
12. Out-of-order `participant_left` (count would go negative) → resets to 0
13. Non-doctor joins, leaves, rejoins before doctor arrives → participants hash handles correctly
14. Doctor leaves twice in quick succession without returning → second leave revokes first grace task

## Migration / rollout

- No DB schema changes.
- New Redis keys are additive; existing keys keep their semantics except `doctor_name` (removed — no reader depends on it after this change).
- `_clear_meeting_keys` extended with the new key suffixes so no orphan keys.
- No feature flag. This is a behavior change for all PMR sessions simultaneously. Acceptable because: (a) current behavior is actively harmful (the incident), (b) the change is additive-guard (the worst case of a bug is "we don't record when we should have", which is a safer failure mode than "we record when we shouldn't have").
- Deploy via existing master-branch CI flow.

## Open questions to resolve in implementation plan

1. Concrete `automatic_leave` timeout values for the Recall safety net.
2. Exact Recall `leave_call` endpoint verification (`POST /bot/{id}/leave_call/`) and error handling for bots already in terminal state.
3. Whether to log at `info` or `debug` level for each state-transition event (info is default).
4. Whether the KQL justfile recipe needs parameter escaping for meeting_ids with special chars (Zoom meeting_ids are numeric, so probably not).

## Estimated cost impact

Given Recall bills `$0.50/hr` from `joining_call` to terminal:
- Before: 1 bot per Zoom session. PMR open 8h → ~$4/day/doctor plus $0.15/hr × actual-recording-hours.
- After: 1 bot per appointment, avg ~20 min per appointment, ~10 appointments/day → ~$1.65/day/doctor including transcription. Also zero cost for accidentally-opened PMRs.

Savings at scale: ~50–70% on Recall bill, plus elimination of phantom appointment/patient records in the DB.
