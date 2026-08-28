import { describe, expect, it, vi } from "vitest"

import { OnboardingZoomStep } from "./OnboardingZoomStep"
import { fireEvent, render, screen } from "@testing-library/react"

const getSessionToken = vi.hoisted(() => vi.fn())

vi.mock("~/configuration/clerk", () => ({ getSessionToken }))

describe("OnboardingZoomStep", () => {
  it("offers an explicit skip to an eligible staging pilot", () => {
    const onComplete = vi.fn()

    render(
      <OnboardingZoomStep onComplete={onComplete} zoomEnabled allowZoomSkip />,
    )

    expect(
      screen.getByRole("button", { name: "Connect Zoom" }),
    ).toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", { name: "Continue without Zoom" }),
    )
    expect(onComplete).toHaveBeenCalledOnce()
    expect(getSessionToken).not.toHaveBeenCalled()
  })

  it("keeps the existing Zoom-required flow when the switch is off", () => {
    render(
      <OnboardingZoomStep
        onComplete={vi.fn()}
        zoomEnabled
        allowZoomSkip={false}
      />,
    )

    expect(
      screen.getByRole("button", { name: "Connect Zoom" }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Continue without Zoom" }),
    ).not.toBeInTheDocument()
  })

  it("preserves the existing coming-soon continuation", () => {
    const onComplete = vi.fn()

    render(
      <OnboardingZoomStep
        onComplete={onComplete}
        zoomEnabled={false}
        allowZoomSkip={false}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Continue" }))
    expect(onComplete).toHaveBeenCalledOnce()
  })
})
