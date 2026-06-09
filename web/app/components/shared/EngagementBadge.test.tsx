import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { EngagementBadge } from "./EngagementBadge"

describe("EngagementBadge", () => {
  it("renders the role name", () => {
    render(<EngagementBadge roleKey="collaborative" roleName="Collaborative" />)
    expect(screen.getByText("Collaborative")).toBeInTheDocument()
  })

  it("applies a distinct color class per role", () => {
    const { container: c1 } = render(
      <EngagementBadge roleKey="self_directed" roleName="Self-Directed" />,
    )
    const { container: c2 } = render(
      <EngagementBadge roleKey="directed" roleName="Directed" />,
    )
    expect(c1.firstElementChild?.className).not.toEqual(
      c2.firstElementChild?.className,
    )
  })

  it("exposes a description for the role (accessible tooltip text)", () => {
    render(<EngagementBadge roleKey="directed" roleName="Directed" />)
    expect(screen.getByTitle(/clinician.*decide|decide.*for|lead and decide/i)).toBeInTheDocument()
  })

  it("falls back gracefully for an unknown role key", () => {
    render(<EngagementBadge roleKey={"???" as never} roleName="Mystery" />)
    expect(screen.getByText("Mystery")).toBeInTheDocument()
  })
})
