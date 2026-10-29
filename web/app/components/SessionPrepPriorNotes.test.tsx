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

  it("uses Open, View, and Update only for their corresponding preparation states", () => {
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
        preparationState="ready"
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "View Session Prep" }))
    expect(open).toHaveBeenCalledTimes(2)
    view.rerender(
      <SessionPrepPriorNotes
        overview={overview}
        onOpenSessionPrep={open}
        preparationState="update_needed"
      />,
    )
    expect(screen.getByText(/Reviewed prior notes changed/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Update Session Prep" }))
    expect(open).toHaveBeenCalledTimes(3)
  })

  it("describes a pending preparation without calling its content unavailable", () => {
    render(
      <SessionPrepPriorNotes
        overview={overview}
        onOpenSessionPrep={vi.fn()}
        preparationState="pending"
      />,
    )
    expect(screen.getByRole("status")).toHaveTextContent(
      "Session Prep is being prepared from 1 reviewed note",
    )
    expect(screen.getByRole("status")).toHaveTextContent(
      "Open it to follow progress",
    )
    expect(screen.queryByText(/unavailable/i)).not.toBeInTheDocument()
  })

  it("refreshes only on request and disables duplicate refresh while loading", () => {
    const result = render(<SessionPrepPriorNotes overview={overview} />)
    fireEvent.click(screen.getByRole("button", { name: "Refresh prior notes" }))
    expect(revalidator.revalidate).toHaveBeenCalledTimes(1)
    revalidator.state = "loading"
    result.rerender(<SessionPrepPriorNotes overview={overview} />)
    expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled()
  })

  it("keeps a new preparation closed until a reviewed source is available", () => {
    const open = vi.fn()
    const result = render(
      <SessionPrepPriorNotes
        overview={{ ...overview, included_count: 0, notes: [] }}
        onOpenSessionPrep={open}
      />,
    )
    expect(
      screen.getByText("No prior visit notes were found in this period."),
    ).toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveTextContent(
      "No reviewed prior notes",
    )
    expect(
      screen.getByRole("button", { name: "Open Session Prep" }),
    ).toBeDisabled()
    expect(screen.getByText(/Review at least one prior note/)).toBeVisible()
    fireEvent.click(screen.getByRole("button", { name: "Open Session Prep" }))
    expect(open).not.toHaveBeenCalled()
    result.rerender(
      <SessionPrepPriorNotes
        overview={{ ...overview, included_count: 0, notes: [] }}
        onOpenSessionPrep={open}
        preparationState="update_needed"
      />,
    )
    expect(
      screen.getByRole("button", { name: "Update Session Prep" }),
    ).toBeDisabled()
    expect(
      screen
        .getAllByRole("status")
        .map((status) => status.textContent)
        .join(" "),
    ).toMatch(/none are currently available.*before updating Session Prep/)
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

  it("keeps an existing brief viewable when no reviewed sources remain", () => {
    const open = vi.fn()
    render(
      <SessionPrepPriorNotes
        overview={{ ...overview, included_count: 0, notes: [] }}
        onOpenSessionPrep={open}
        preparationState="ready"
      />,
    )
    expect(screen.getByText(/existing brief remains available/)).toBeVisible()
    expect(
      screen.getByText(/Previous Session Prep answers remain/),
    ).toHaveTextContent("save an answer as Markdown")
    fireEvent.click(screen.getByRole("button", { name: "View Session Prep" }))
    expect(open).toHaveBeenCalledOnce()
  })
})
