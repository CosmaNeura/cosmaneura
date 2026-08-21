import { describe, expect, it, vi } from "vitest"

import { SetupCard } from "./settings-layout"
import { fireEvent, render, screen } from "@testing-library/react"

describe("SetupCard", () => {
  it("jumps to the section for an open item", () => {
    const onJump = vi.fn()

    render(
      <SetupCard
        items={[
          {
            id: "bio",
            label: "Biography",
            status: "done",
            note: "Used to personalize intake.",
            section: "biography",
          },
          {
            id: "zoom",
            label: "Zoom",
            status: "open",
            note: "Not connected.",
            section: "zoom",
          },
        ]}
        onJump={onJump}
      />,
    )

    expect(screen.getByText("1 of 2")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Zoom/ }))
    expect(onJump).toHaveBeenCalledWith("zoom")
  })
})
