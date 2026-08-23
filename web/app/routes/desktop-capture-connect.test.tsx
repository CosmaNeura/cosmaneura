import { beforeEach, describe, expect, it, vi } from "vitest"

import DesktopCaptureConnectPage from "./desktop-capture-connect"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const mocks = vi.hoisted(() => ({
  authorize: vi.fn(),
  takeHandoff: vi.fn(),
}))

vi.mock("~/configuration/bootstrap-desktop-capture", () => ({
  takeDesktopCaptureHandoff: mocks.takeHandoff,
}))
vi.mock("~/configuration/client", () => ({
  authorizeDesktopCaptureLaunch: mocks.authorize,
}))

const secret = (value: string) => value.repeat(43)

function validFragment() {
  return new URLSearchParams({
    launch_ticket: secret("a"),
    code_challenge: secret("b"),
    code_challenge_method: "S256",
    redirect_uri: "cosmaneura-capture://oauth/callback",
    installation_thumbprint: secret("c"),
    state: secret("d"),
  }).toString()
}

describe("DesktopCaptureConnectPage", () => {
  beforeEach(() => {
    mocks.authorize.mockReset()
    mocks.takeHandoff.mockReset()
    mocks.takeHandoff.mockReturnValue(validFragment())
    mocks.authorize.mockResolvedValue({
      error: { detail: "synthetic failure" },
    })
  })

  it("sends only the fixed PKCE authorization fields to the platform", async () => {
    render(<DesktopCaptureConnectPage />)

    fireEvent.click(
      screen.getByRole("button", { name: "Verify and open Cosmas Capture" }),
    )

    await waitFor(() => expect(mocks.authorize).toHaveBeenCalledOnce())
    expect(mocks.authorize).toHaveBeenCalledWith({
      body: {
        launch_ticket: secret("a"),
        code_challenge: secret("b"),
        code_challenge_method: "S256",
        redirect_uri: "cosmaneura-capture://oauth/callback",
        installation_thumbprint: secret("c"),
      },
    })
    expect(mocks.authorize.mock.calls[0]?.[0].body).not.toHaveProperty("state")
    expect(screen.getByText(/invalid or expired/i)).toBeInTheDocument()
  })

  it("fails closed without calling the platform for malformed input", () => {
    mocks.takeHandoff.mockReturnValue("launch_ticket=unsafe")

    render(<DesktopCaptureConnectPage />)

    expect(screen.getByText(/invalid or expired/i)).toBeInTheDocument()
    expect(mocks.authorize).not.toHaveBeenCalled()
  })
})
