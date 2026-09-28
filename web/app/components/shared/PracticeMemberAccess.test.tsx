import { describe, expect, it } from "vitest"

import type { PracticeStatus } from "~/lib/practice-api"

import { PracticeAccessEndingNotice } from "./PracticeMemberAccess"
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
        "You keep access until midnight Eastern tonight.",
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

  it("renders nothing without a scheduled end", () => {
    const { container } = render(
      <PracticeAccessEndingNotice practice={status({})} />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})
