import { describe, expect, it } from "vitest"

import { StatusBadge } from "./StatusBadge"
import { render, screen } from "@testing-library/react"

describe("StatusBadge", () => {
  it("uses a context label when given one", () => {
    render(<StatusBadge status="not-started" label="Upcoming" />)
    expect(screen.getByText("Upcoming")).toBeInTheDocument()
  })

  it("does not style a pending state as an error", () => {
    render(<StatusBadge status="not-started" />)
    const badge = screen.getByText("Not started")
    expect(badge.className).not.toMatch(/error|destructive/)
    expect(badge).toHaveClass("bg-muted")
  })

  it("has a variant for the navy sidebar", () => {
    render(<StatusBadge status="completed" surface="navy" />)
    expect(screen.getByText("Completed")).toHaveClass("text-sidebar-foreground")
  })
})
