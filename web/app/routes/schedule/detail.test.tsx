import { RouterProvider, createMemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type {
  Appointment,
  Patient,
  ZoomMeetingResponse,
} from "~/configuration/client"

import { MeetingDetail } from "./detail"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
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
  it("uses the doctor's terminology for consent copy", async () => {
    settingsData.mockResolvedValue({ terminology: "therapist_client" })

    renderMeeting()

    expect(
      await screen.findByText(/Your client will still be asked/),
    ).toBeInTheDocument()
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
