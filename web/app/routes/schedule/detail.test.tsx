import { RouterProvider, createMemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type {
  Appointment,
  Patient,
  ZoomMeetingResponse,
} from "~/configuration/client"
import type { MeetingNoteAvailability } from "~/lib/meeting-note-availability"

import { MeetingDetail, clientLoader } from "./detail"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const authorizeZoomCapture = vi.fn()
const cancelMeeting = vi.fn()
const noteDetail = vi.fn()
const scheduleList = vi.fn()
const noteList = vi.fn()
const patientList = vi.fn()
const toast = vi.fn()

vi.mock("~/configuration/client", () => ({
  authorizeZoomCapture: (...args: unknown[]) => authorizeZoomCapture(...args),
  cancelMeeting: (...args: unknown[]) => cancelMeeting(...args),
  noteDetail: (...args: unknown[]) => noteDetail(...args),
  scheduleList: (...args: unknown[]) => scheduleList(...args),
  noteList: (...args: unknown[]) => noteList(...args),
  patientList: (...args: unknown[]) => patientList(...args),
}))

vi.mock("~/hooks/use-toast", () => ({
  toast: (...args: unknown[]) => toast(...args),
}))

const settingsData = vi.fn()

vi.mock("client/@tanstack/react-query.gen", () => ({
  settingsDataOptions: () => ({
    queryKey: ["settingsData"],
    queryFn: () => settingsData(),
  }),
}))

const meeting: ZoomMeetingResponse = {
  id: "zmtg_test",
  zoom_meeting_id: "123456789",
  topic: "Follow-up visit",
  start_time: "2026-08-28T14:00:00Z",
  duration: 30,
  join_url: "https://zoom.us/j/123456789",
  status: "scheduled",
  appointment_id: null,
  capture_authorized: false,
}

const appointment: Appointment = {
  id: "appointment_test",
  doctor_id: "doctor_test",
  patient_id: "patient_test",
  created_at: "2026-08-01T12:00:00Z",
  updated_at: "2026-08-01T12:00:00Z",
  created_by_system: "acuity",
  external_id: "external_test",
  appointment_date: "2026-08-28T14:00:00Z",
  appointment_name: null,
  appointment_notes_preview: null,
  patient_name: "Test Patient",
  intake_status: "none",
}

const patient: Patient = {
  id: "patient_test",
  doctor_id: "doctor_test",
  created_at: "2026-08-01T12:00:00Z",
  updated_at: "2026-08-01T12:00:00Z",
  name: "Test Patient",
  date_of_birth: "1990-02-03T00:00:00Z",
}

function renderMeeting(
  currentMeeting = meeting,
  appointments: Appointment[] = [appointment],
  patients: Patient[] = [patient],
  captureEnabled = true,
  noteAvailability: MeetingNoteAvailability | null = null,
) {
  const loader = vi.fn(() => null)
  const router = createMemoryRouter(
    [
      {
        path: "/",
        loader,
        hydrateFallbackElement: <div>Loading meeting</div>,
        element: (
          <MeetingDetail
            meeting={currentMeeting}
            appointments={appointments}
            captureEnabled={captureEnabled}
            patients={patients}
            noteAvailability={noteAvailability}
          />
        ),
      },
      { path: "/schedule", element: <p>Schedule index</p> },
    ],
    { initialEntries: ["/"] },
  )

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )

  return { loader }
}

beforeEach(() => {
  authorizeZoomCapture.mockReset()
  cancelMeeting.mockReset()
  noteDetail.mockReset()
  scheduleList.mockReset()
  noteList.mockReset()
  patientList.mockReset()
  toast.mockReset()
  settingsData.mockReset()
  settingsData.mockResolvedValue({ terminology: "doctor_patient" })
})

describe("meeting cancellation", () => {
  it("asks before cancelling and keeps the meeting by default", async () => {
    renderMeeting(meeting, [appointment], [patient], false)

    fireEvent.click(
      await screen.findByRole("button", { name: "Cancel meeting" }),
    )

    const dialog = await screen.findByRole("alertdialog")
    expect(dialog).toHaveAccessibleName(/^Cancel your .+ meeting\?$/)
    expect(dialog).toHaveAccessibleDescription(
      /The meeting link will stop working for your patient/,
    )
    fireEvent.click(screen.getByRole("button", { name: "Keep meeting" }))

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
    expect(cancelMeeting).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Cancel meeting" })).toHaveFocus()
  })

  it("cancels after confirmation", async () => {
    cancelMeeting.mockResolvedValue({ data: {}, error: undefined })
    renderMeeting(meeting, [appointment], [patient], false)

    fireEvent.click(
      await screen.findByRole("button", { name: "Cancel meeting" }),
    )
    fireEvent.click(
      await screen.findByRole("button", { name: "Yes, cancel meeting" }),
    )

    await waitFor(() =>
      expect(cancelMeeting).toHaveBeenCalledWith({
        path: { zoom_meeting_id: "123456789" },
      }),
    )
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith({ title: "Meeting cancelled" }),
    )
    expect(await screen.findByText("Schedule index")).toBeInTheDocument()
  })

  it("keeps the dialog open when cancelling fails", async () => {
    cancelMeeting.mockResolvedValue({ data: undefined, error: { detail: "x" } })
    renderMeeting(meeting, [appointment], [patient], false)

    fireEvent.click(
      await screen.findByRole("button", { name: "Cancel meeting" }),
    )
    fireEvent.click(
      await screen.findByRole("button", { name: "Yes, cancel meeting" }),
    )

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Couldn't cancel the meeting" }),
      ),
    )
    expect(screen.getByRole("alertdialog")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Yes, cancel meeting" }),
    ).toBeEnabled()
  })
})

describe("meeting bot copy", () => {
  it("does not claim notes exist just because an ended meeting links a visit", async () => {
    renderMeeting({
      ...meeting,
      status: "ended",
      appointment_id: appointment.id,
    })

    await screen.findByText(meeting.topic)
    expect(screen.queryByText(/AI notes are available/)).toBeNull()
    expect(screen.getByRole("link", { name: "Open visit →" })).toHaveAttribute(
      "href",
      `/notes/${appointment.id}`,
    )
  })

  it.each([
    [
      "missing",
      "No note is available for the linked visit yet.",
      "Open visit →",
    ],
    ["unavailable", "Note availability could not be checked.", "Open visit →"],
    ["ready", "Notes are available for the linked visit.", "View notes →"],
  ] as const)(
    "shows %s from the checked visit without claiming Zoom recorded it",
    async (availability, copy, link) => {
      renderMeeting(
        { ...meeting, status: "ended", appointment_id: appointment.id },
        [appointment],
        [patient],
        false,
        availability,
      )
      expect(
        await screen.findByText(`This meeting has ended. ${copy}`),
      ).toBeVisible()
      expect(screen.getByRole("link", { name: link })).toHaveAttribute(
        "href",
        `/notes/${appointment.id}`,
      )
      expect(
        screen.queryByText(/processing|AI notes|recording succeeded/i),
      ).toBeNull()
    },
  )

  it("does not promise notes for an unlinked ended meeting", async () => {
    renderMeeting({ ...meeting, status: "ended" })
    expect(
      await screen.findByText("This meeting has ended. No visit is linked."),
    ).toBeVisible()
    expect(screen.queryByRole("link", { name: /notes|visit/ })).toBeNull()
    expect(screen.queryByText(/Notes will appear/)).toBeNull()
  })

  it("uses the doctor's terminology for consent copy", async () => {
    settingsData.mockResolvedValue({ terminology: "therapist_client" })

    renderMeeting()

    expect(
      await screen.findByText(/Your client will still be asked/),
    ).toBeInTheDocument()
  })
})

describe("schedule loader note availability", () => {
  function load() {
    return clientLoader({
      params: { zoom_meeting_id: meeting.zoom_meeting_id },
      request: new Request(
        `https://app.example/schedule/${meeting.zoom_meeting_id}`,
      ),
    } as Parameters<typeof clientLoader>[0])
  }

  it("checks exactly the ended linked visit and discards its content", async () => {
    scheduleList.mockResolvedValue({
      data: {
        meetings: [
          { ...meeting, status: "ended", appointment_id: appointment.id },
        ],
      },
    })
    noteDetail.mockResolvedValue({
      data: { appointment, notes: [] },
      response: new Response(null, { status: 200 }),
    })
    const data = await load()
    expect(data.noteAvailability).toBe("missing")
    expect(noteDetail).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        path: { appointment_id: appointment.id },
        signal: expect.any(AbortSignal),
      }),
    )
    expect(data).not.toHaveProperty("notes")
    expect(data).not.toHaveProperty("transcript")
    expect(noteList).not.toHaveBeenCalled()
    expect(patientList).not.toHaveBeenCalled()
  })

  it.each(["scheduled", "started", "cancelled", "ended"] as const)(
    "does not read a note for an unlinked %s meeting",
    async (status) => {
      scheduleList.mockResolvedValue({
        data: { meetings: [{ ...meeting, status }] },
      })
      expect((await load()).noteAvailability).toBeNull()
      expect(noteDetail).not.toHaveBeenCalled()
    },
  )

  it.each(["scheduled", "started", "cancelled"] as const)(
    "does not infer notes from a linked %s meeting",
    async (status) => {
      scheduleList.mockResolvedValue({
        data: {
          meetings: [{ ...meeting, status, appointment_id: appointment.id }],
        },
      })
      expect((await load()).noteAvailability).toBeNull()
      expect(noteDetail).not.toHaveBeenCalled()
    },
  )

  it("preserves the ordinary active-meeting authorization options", async () => {
    scheduleList.mockResolvedValue({
      data: { capture_available: true, meetings: [meeting] },
    })
    noteList.mockResolvedValue({ data: { appointments: [appointment] } })
    patientList.mockResolvedValue({ data: [patient] })
    const data = await load()
    expect(data.appointments).toEqual([appointment])
    expect(data.patients).toEqual([patient])
    expect(data.appointmentsUnavailable).toBe(false)
    expect(noteDetail).not.toHaveBeenCalled()
  })
})

describe("meeting bot authorization", () => {
  it("shows an unknown birthday while selecting the exact existing appointment", async () => {
    authorizeZoomCapture.mockResolvedValue({
      data: { state: "armed" },
      error: undefined,
    })
    renderMeeting(meeting, [appointment], [{ ...patient, date_of_birth: null }])
    fireEvent.click(
      await screen.findByRole("button", { name: "Enable meeting bot" }),
    )
    expect(
      screen.getByRole("option", { name: /Test Patient — DOB not provided/ }),
    ).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Cosmas appointment"), {
      target: { value: "appointment_test" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Confirm and enable" }))
    await waitFor(() =>
      expect(authorizeZoomCapture).toHaveBeenCalledWith({
        path: { zoom_meeting_id: "123456789" },
        body: { appointment_id: "appointment_test" },
      }),
    )
  })

  it("requires an existing appointment and authorizes the selected Zoom meeting", async () => {
    authorizeZoomCapture.mockResolvedValue({
      data: { appointment_id: "appt_test", state: "armed" },
      error: undefined,
    })
    const { loader } = renderMeeting()

    fireEvent.click(
      await screen.findByRole("button", { name: "Enable meeting bot" }),
    )

    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("data-ph-no-capture")
    expect(dialog).toHaveAttribute("data-sentry-block")

    const confirmButton = screen.getByRole("button", {
      name: "Confirm and enable",
    })
    expect(confirmButton).toBeDisabled()
    expect(screen.queryByLabelText("Patient name")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Date of birth")).not.toBeInTheDocument()
    expect(
      screen.getByRole("option", {
        name: /Test Patient — DOB Feb 3, 1990/,
      }),
    ).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText("Cosmas appointment"), {
      target: { value: "appointment_test" },
    })
    fireEvent.click(confirmButton)

    await waitFor(() => {
      expect(authorizeZoomCapture).toHaveBeenCalledWith({
        path: { zoom_meeting_id: "123456789" },
        body: {
          appointment_id: "appointment_test",
        },
      })
    })
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2))

    expect(toast).toHaveBeenCalledWith({
      title: "Cosmas meeting bot enabled",
    })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("does not expose capture outside the existing Zoom allowlist", async () => {
    renderMeeting(meeting, [appointment], [patient], false)

    expect(
      await screen.findByRole("heading", { name: "Follow-up visit" }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Enable meeting bot" }),
    ).not.toBeInTheDocument()
  })
})

describe("terminal meeting bot status", () => {
  it.each([
    ["permission_denied", "Zoom recording permission denied"],
    ["failed", "Meeting bot could not continue"],
    ["ended", "Meeting bot has left the meeting"],
  ] as const)(
    "shows %s instead of a green enabled claim",
    async (capture_status, heading) => {
      renderMeeting({
        ...meeting,
        status: "started",
        capture_authorized: true,
        capture_status,
      })
      expect(await screen.findByText(heading)).toBeInTheDocument()
      expect(
        screen.queryByText("Cosmas meeting bot enabled"),
      ).not.toBeInTheDocument()
      expect(
        screen.getByText(/will not restart automatically/),
      ).toBeInTheDocument()
      expect(authorizeZoomCapture).not.toHaveBeenCalled()
    },
  )

  it("retains the permission failure when the meeting has ended", async () => {
    renderMeeting({
      ...meeting,
      status: "ended",
      capture_authorized: true,
      capture_status: "permission_denied",
    })
    expect(
      await screen.findByText("Zoom recording permission denied"),
    ).toBeInTheDocument()
  })
})
