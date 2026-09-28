import { describe, expect, it } from "vitest"

import type { PracticeStatus } from "~/lib/practice-api"

import {
  PracticeAccessEndingNotice,
  localAccessEnd,
} from "./PracticeMemberAccess"
import { render, screen } from "@testing-library/react"

function status(fields: Partial<PracticeStatus>) {
  return {
    access_ends_at: null,
    access_ended: false,
    access_revoked: false,
    ...fields,
  } as PracticeStatus
}

describe("PracticeAccessEndingNotice", () => {
  it("tells a revoked clinician they keep access until tonight", () => {
    render(
      <PracticeAccessEndingNotice
        practice={status({ access_ends_at: "2026-09-28T04:00:00Z" })}
      />,
    )

    expect(screen.getByRole("status")).toHaveTextContent(
      "Your practice owner ended your access to Cosmas." +
        "You keep access until midnight Eastern tonight " +
        `(${localAccessEnd("2026-09-28T04:00:00Z")} your time).`,
    )
  })

  it("says the access has ended once the server says so", () => {
    render(
      <PracticeAccessEndingNotice
        practice={status({
          access_ends_at: "2026-09-28T04:00:00Z",
          access_ended: true,
        })}
      />,
    )

    expect(screen.getByRole("status")).toHaveTextContent(
      "Ask your practice owner if you need it restored.",
    )
  })

  it("says nothing of the owner once support permanently revoked access", () => {
    const { container } = render(
      <PracticeAccessEndingNotice
        practice={status({
          access_ends_at: "2026-09-28T04:00:00Z",
          access_ended: true,
          access_revoked: true,
        })}
      />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing without a scheduled end", () => {
    const { container } = render(
      <PracticeAccessEndingNotice practice={status({})} />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})

describe("localAccessEnd", () => {
  it("shows the Eastern cut-off in the viewer's own clock", () => {
    const shown = (zone: string) =>
      localAccessEnd("2026-09-28T04:00:00Z", zone).replace(/\s/g, " ")

    expect(shown("America/Los_Angeles")).toBe("Sun, 9:00 PM PDT")
    expect(shown("America/New_York")).toBe("Mon, 12:00 AM EDT")
  })
})
