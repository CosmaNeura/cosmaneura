import { describe, expect, it } from "vitest"

import type { PracticeStatus } from "~/lib/practice-api"

import {
  PracticeAccessEndingNotice,
  practiceAccessEnded,
} from "./PracticeMemberAccess"
import { render, screen } from "@testing-library/react"

const NOW = new Date("2026-09-27T22:00:00Z")

function status(access_ends_at: string | null) {
  return { access_ends_at } as PracticeStatus
}

describe("PracticeAccessEndingNotice", () => {
  it("tells a revoked clinician they keep access until tonight", () => {
    render(
      <PracticeAccessEndingNotice
        practice={status("2026-09-28T04:00:00Z")}
        now={NOW}
      />,
    )

    expect(screen.getByRole("status")).toHaveTextContent(
      "Your practice owner ended your access to Cosmas." +
        "You keep access until midnight Eastern tonight.",
    )
  })

  it("says the access has ended once the cut-off passed", () => {
    render(
      <PracticeAccessEndingNotice
        practice={status("2026-09-27T04:00:00Z")}
        now={NOW}
      />,
    )

    expect(screen.getByRole("status")).toHaveTextContent(
      "Ask your practice owner if you need it restored.",
    )
  })

  it("renders nothing without a scheduled end", () => {
    const { container } = render(
      <PracticeAccessEndingNotice practice={status(null)} now={NOW} />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})

describe("practiceAccessEnded", () => {
  it("is true only at or after the scheduled end", () => {
    expect(practiceAccessEnded(status(null), NOW)).toBe(false)
    expect(practiceAccessEnded(status("2026-09-28T04:00:00Z"), NOW)).toBe(false)
    expect(practiceAccessEnded(status("2026-09-27T22:00:00Z"), NOW)).toBe(true)
    expect(practiceAccessEnded(null, NOW)).toBe(false)
  })
})
