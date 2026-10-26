import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { NoteDetail } from "~/configuration/client"
import { BillingRecoveryRedirectError } from "~/lib/billing-recovery"
import { loadMeetingNoteAvailability } from "~/lib/meeting-note-availability"
import { PRACTICE_BAA_REQUIRED_DETAIL } from "~/lib/practice-gate-error"
import { READ_DEADLINE_MS } from "~/lib/read-deadline"

const { read } = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock("~/configuration/client", () => ({ noteDetail: read }))

const request = () => new Request("https://app.example/schedule/123")
const emptyVisit: NoteDetail = {
  appointment: {
    id: "appointment_test",
    doctor_id: "doctor_test",
    patient_id: "patient_test",
    external_id: null,
    appointment_date: "2026-10-26",
    patient_name: "Synthetic patient",
    intake_status: "none",
  },
  patient: {
    id: "patient_test",
    name: "Synthetic patient",
    doctor_id: "doctor_test",
  },
  notes: [],
  transcript: null,
}
const note = {
  id: "visit_note_test",
  doctor_id: "doctor_test",
  appointment_id: "appointment_test",
  ai_transcript_id: "transcript_test",
  doctor_note_schema_id: "schema_test",
  note_type: "SOAP",
  note: { Subjective: "Synthetic note" },
}
const response = (data: NoteDetail) => ({
  data,
  response: new Response(null, { status: 200 }),
})

beforeEach(() => {
  read.mockReset()
})
afterEach(() => vi.useRealTimers())

describe("current linked-visit note availability", () => {
  it.each([{ notes: [] }, { notes: null }])(
    "does not infer a note from an empty read ($notes)",
    async ({ notes }) => {
      read.mockResolvedValue(response({ ...emptyVisit, notes }))
      expect(
        await loadMeetingNoteAvailability("appointment_test", request()),
      ).toBe("missing")
      expect(read).toHaveBeenCalledExactlyOnceWith({
        throwOnError: false,
        path: { appointment_id: "appointment_test" },
        signal: expect.any(AbortSignal),
      })
    },
  )

  it("does not interpret a malformed response as an empty visit", async () => {
    read.mockResolvedValue({ data: { appointment: emptyVisit.appointment } })
    expect(
      await loadMeetingNoteAvailability("appointment_test", request()),
    ).toBe("unavailable")
  })

  it("does not infer notes from a transcript or native workflow binding", async () => {
    read.mockResolvedValue(
      response({
        ...emptyVisit,
        capture_workflow_required: true,
        transcript: {
          id: "transcript_test",
          doctor_id: "doctor_test",
          patient_id: "patient_test",
          appointment_id: "appointment_test",
          duration: 10,
          transcript_data: [],
        },
      }),
    )
    expect(
      await loadMeetingNoteAvailability("appointment_test", request()),
    ).toBe("missing")
  })

  it("reports a current note and returns no clinical content", async () => {
    read.mockResolvedValue(response({ ...emptyVisit, notes: [note] }))
    expect(
      await loadMeetingNoteAvailability("appointment_test", request()),
    ).toBe("ready")
  })

  it.each([
    {
      ...emptyVisit,
      appointment: { ...emptyVisit.appointment, id: "other_visit" },
      notes: [note],
    },
    { ...emptyVisit, notes: [{ ...note, appointment_id: "other_visit" }] },
    { ...emptyVisit, notes: [{ ...note, doctor_id: "other_doctor" }] },
  ])("refuses mismatched visit/owner evidence", async (detail) => {
    read.mockResolvedValue(response(detail))
    expect(
      await loadMeetingNoteAvailability("appointment_test", request()),
    ).toBe("unavailable")
  })

  it.each([403, 404, 503])(
    "does not call a failed read (%s) an empty or ready note",
    async (status) => {
      read.mockResolvedValue({
        response: new Response(null, { status }),
        error: {},
      })
      expect(
        await loadMeetingNoteAvailability("appointment_test", request()),
      ).toBe("unavailable")
    },
  )

  it("bounds a hanging read without retrying or treating late data as success", async () => {
    vi.useFakeTimers()
    let complete!: (value: unknown) => void
    read.mockReturnValue(
      new Promise((resolve) => {
        complete = resolve
      }),
    )
    const loading = loadMeetingNoteAvailability("appointment_test", request())
    await vi.advanceTimersByTimeAsync(READ_DEADLINE_MS)
    expect(await loading).toBe("unavailable")
    expect(read.mock.calls[0][0].signal.aborted).toBe(true)
    complete(response({ ...emptyVisit, notes: [note] }))
    expect(read).toHaveBeenCalledTimes(1)
  })

  it("propagates cancellation instead of returning status for an old navigation", async () => {
    const controller = new AbortController()
    read.mockReturnValue(new Promise(() => {}))
    const loading = loadMeetingNoteAvailability(
      "appointment_test",
      new Request(request(), { signal: controller.signal }),
    )
    const assertion = expect(loading).rejects.toMatchObject({
      name: "AbortError",
    })
    controller.abort()
    await assertion
  })

  it("preserves session, billing and practice recovery", async () => {
    read.mockResolvedValue({
      response: new Response(null, { status: 401 }),
      error: {},
    })
    await expect(
      loadMeetingNoteAvailability("appointment_test", request()),
    ).rejects.toMatchObject({ data: { noteLoadFailure: "session" } })
    const billing = new BillingRecoveryRedirectError("/settings")
    read.mockResolvedValue({ error: billing })
    await expect(
      loadMeetingNoteAvailability("appointment_test", request()),
    ).rejects.toBe(billing)
    read.mockResolvedValue({
      response: new Response(null, { status: 403 }),
      error: { detail: PRACTICE_BAA_REQUIRED_DETAIL },
    })
    await expect(
      loadMeetingNoteAvailability("appointment_test", request()),
    ).rejects.toMatchObject({ data: { kind: "accept_agreement" } })
  })
})
