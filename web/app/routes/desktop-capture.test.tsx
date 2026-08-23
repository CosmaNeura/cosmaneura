import { beforeEach, describe, expect, it, vi } from "vitest"

import { DesktopCaptureForm } from "./desktop-capture"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const mocks = vi.hoisted(() => ({
  createLaunch: vi.fn(),
  noteList: vi.fn(),
}))

vi.mock("~/configuration/client", () => ({
  createDesktopCaptureLaunch: mocks.createLaunch,
  noteList: mocks.noteList,
}))

const appointment = {
  id: "appointment_01SYNTHETIC",
  doctor_id: "doctor_01SYNTHETIC",
  patient_id: "patient_01SYNTHETIC",
  external_id: null,
  appointment_date: "2026-08-24T14:00:00Z",
  patient_name: "Synthetic Patient",
  intake_status: "none" as const,
}

describe("DesktopCapturePage", () => {
  beforeEach(() => {
    mocks.createLaunch.mockReset()
    mocks.createLaunch.mockResolvedValue({
      error: { detail: "desktop capture is disabled" },
    })
  })

  it("requires explicit consent and sends a canonical encounter attestation", async () => {
    render(<DesktopCaptureForm appointments={[appointment]} />)

    const launchButton = screen.getByRole("button", {
      name: "Verify consent and open desktop app",
    })
    expect(launchButton).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/Participant states or regions/), {
      target: { value: "ny, US-NJ" },
    })
    fireEvent.click(
      screen.getByLabelText(/I attest that every participant consented/i),
    )
    fireEvent.click(launchButton)

    await waitFor(() => expect(mocks.createLaunch).toHaveBeenCalledOnce())
    const request = mocks.createLaunch.mock.calls[0]?.[0]
    expect(request.path).toEqual({ appointment_id: appointment.id })
    expect(request.body).toMatchObject({
      modality: "virtual",
      participant_regions: ["US-NY", "US-NJ"],
      method: "verbal_attestation",
      all_parties_consented: true,
    })
    expect(request.headers["Idempotency-Key"]).toMatch(/^desktop-launch-/)
    expect(
      screen.getByText(/could not prepare this capture/i),
    ).toBeInTheDocument()
  })

  it("rejects ambiguous region names before an API call", async () => {
    render(<DesktopCaptureForm appointments={[appointment]} />)

    fireEvent.change(screen.getByLabelText(/Participant states or regions/), {
      target: { value: "New York" },
    })
    fireEvent.click(
      screen.getByLabelText(/I attest that every participant consented/i),
    )
    fireEvent.click(
      screen.getByRole("button", {
        name: "Verify consent and open desktop app",
      }),
    )

    await screen.findByText(/could not prepare this capture/i)
    expect(mocks.createLaunch).not.toHaveBeenCalled()
  })
})
