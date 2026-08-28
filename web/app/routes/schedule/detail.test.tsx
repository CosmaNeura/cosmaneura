import { RouterProvider, createMemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type {
  Appointment,
  Patient,
  ZoomMeetingResponse,
} from "~/configuration/client"

import { MeetingDetail } from "./detail"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const authorizeZoomCapture = vi.fn()
const cancelMeeting = vi.fn()
const toast = vi.fn()

vi.mock("~/configuration/client", () => ({
  authorizeZoomCapture: (...args: unknown[]) => authorizeZoomCapture(...args),
  cancelMeeting: (...args: unknown[]) => cancelMeeting(...args),
}))

vi.mock("~/hooks/use-toast", () => ({
  toast: (...args: unknown[]) => toast(...args),
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
) {
  const loader = vi.fn(() => null)
  const router = createMemoryRouter(
    [
      {
        path: "/",
        loader,
        element: (
          <MeetingDetail
            meeting={currentMeeting}
            appointments={appointments}
            captureEnabled={captureEnabled}
            patients={patients}
          />
        ),
      },
    ],
    { initialEntries: ["/"] },
  )

  render(<RouterProvider router={router} />)

  return { loader }
}

beforeEach(() => {
  authorizeZoomCapture.mockReset()
  cancelMeeting.mockReset()
  toast.mockReset()
})

describe("meeting bot authorization", () => {
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
