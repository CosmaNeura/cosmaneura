import { describe, expect, it, vi } from "vitest"

import { OnboardingZoomStep } from "./OnboardingZoomStep"
import { fireEvent, render, screen } from "@testing-library/react"

const getClient = vi.hoisted(() => vi.fn())

vi.mock("~/configuration/clerk", () => ({ getClient }))

describe("OnboardingZoomStep", () => {
  it("leads eligible beta users with the available recording workflow", () => {
    const onComplete = vi.fn()

    render(<OnboardingZoomStep onComplete={onComplete} zoomEnabled />)

    expect(
      screen.getByRole("heading", { name: "Start with a visit recording" }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/review and edit the generated note/i),
    ).toBeInTheDocument()
    expect(screen.getByText("Beta access")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Connect Zoom" }),
    ).toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", {
        name: "Continue with recording uploads",
      }),
    )
    expect(onComplete).toHaveBeenCalledOnce()
    expect(getClient).not.toHaveBeenCalled()
  })

  it("keeps the coming-soon continuation for non-allowlisted users", () => {
    const onComplete = vi.fn()

    render(<OnboardingZoomStep onComplete={onComplete} zoomEnabled={false} />)

    expect(
      screen.getByText(
        "The Zoom meeting bot is an optional beta and is coming soon for most accounts.",
      ),
    ).toBeInTheDocument()
    expect(screen.getByText("Coming Soon")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Connect Zoom" }),
    ).not.toBeInTheDocument()

    fireEvent.click(
      screen.getByRole("button", {
        name: "Continue with recording uploads",
      }),
    )
    expect(onComplete).toHaveBeenCalledOnce()
    expect(getClient).not.toHaveBeenCalled()
  })
})
