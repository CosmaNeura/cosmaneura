import { beforeEach, describe, expect, it, vi } from "vitest"

import type { PriorNoteOverview } from "~/configuration/client"

import { SessionPrepPriorNotes } from "./SessionPrepPriorNotes"
import { fireEvent, render, screen } from "@testing-library/react"

const revalidator = vi.hoisted(() => ({ state: "idle", revalidate: vi.fn() }))
vi.mock("react-router", () => ({ useRevalidator: () => revalidator }))

const overview: PriorNoteOverview = {
  included_count: 1,
  more_notes: false,
  selection_limited: false,
  notes: [
    {
      note_id: "visit_note_reviewed",
      appointment_id: "appointment_prior",
      appointment_date: "2026-09-20T12:00:00Z",
      note_type: "SOAP",
      status: "included",
    },
    {
      note_id: "visit_note_draft",
      appointment_id: "appointment_other",
      appointment_date: "2026-09-19T12:00:00Z",
      note_type: "Summary",
      status: "needs_review",
    },
  ],
}

describe("prior visits for Session Prep", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    revalidator.state = "idle"
  })

  it("distinguishes included notes from notes requiring review and links to the exact note", () => {
    render(<SessionPrepPriorNotes overview={overview} />)
    expect(screen.getByRole("status")).toHaveTextContent(
      "1 reviewed note is available",
    )
    expect(
      screen.getByText("Included in the next preparation"),
    ).toBeInTheDocument()
    expect(screen.getByText("Needs your review")).toBeInTheDocument()
    const links = screen.getAllByRole("link", {
      name: /Open note .* in a new tab/,
    })
    expect(links[1]).toHaveAttribute(
      "href",
      "/notes/appointment_other?reviewNote=visit_note_draft#note-visit_note_draft",
    )
    expect(links[1]).toHaveAttribute("target", "_blank")
    expect(links[1]).toHaveAttribute("rel", "noopener noreferrer")
    expect(revalidator.revalidate).not.toHaveBeenCalled()
    expect(
      screen.queryByRole("button", { name: /Mark reviewed/ }),
    ).not.toBeInTheDocument()
  })

  it("opens preparation directly and distinguishes an update-needed source set", () => {
    const open = vi.fn()
    const view = render(
      <SessionPrepPriorNotes overview={overview} onOpenSessionPrep={open} />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Open Session Prep" }))
    expect(open).toHaveBeenCalledTimes(1)
    view.rerender(
      <SessionPrepPriorNotes
        overview={overview}
        onOpenSessionPrep={open}
        preparationState="update_needed"
      />,
    )
    expect(screen.getByText(/Reviewed prior notes changed/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Update Session Prep" }))
    expect(open).toHaveBeenCalledTimes(2)
  })

  it("refreshes only on request and disables duplicate refresh while loading", () => {
    const result = render(<SessionPrepPriorNotes overview={overview} />)
    fireEvent.click(screen.getByRole("button", { name: "Refresh prior notes" }))
    expect(revalidator.revalidate).toHaveBeenCalledTimes(1)
    revalidator.state = "loading"
    result.rerender(<SessionPrepPriorNotes overview={overview} />)
    expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled()
  })

  it("explains empty history and selection limits without claiming a prepared brief", () => {
    const result = render(
      <SessionPrepPriorNotes
        overview={{ ...overview, included_count: 0, notes: [] }}
      />,
    )
    expect(
      screen.getByText("No prior visit notes were found in this period."),
    ).toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveTextContent(
      "No reviewed prior notes",
    )
    result.rerender(
      <SessionPrepPriorNotes
        overview={{ ...overview, more_notes: true, selection_limited: true }}
      />,
    )
    expect(screen.getByText(/20 most recent notes plus/)).toBeInTheDocument()
    expect(
      screen.getByText(/Additional reviewed notes are outside/),
    ).toBeInTheDocument()
  })
})
