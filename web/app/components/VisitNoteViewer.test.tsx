import { describe, expect, it, vi } from "vitest"

import {
  type AiVisitNote,
  reviewNoteForSessionPrep,
} from "~/configuration/client"

import VisitNoteViewer from "./VisitNoteViewer"
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"

vi.mock("~/configuration/client", () => ({
  prepareBillingHeaderOperation: vi.fn(),
  reviewNoteForSessionPrep: vi.fn(),
}))

describe("VisitNoteViewer", () => {
  it("copies every visible note section as plain text", async () => {
    const note: AiVisitNote = {
      id: "visit_note_test",
      doctor_id: "doctor_test",
      appointment_id: "appointment_test",
      ai_transcript_id: "ai_visit_transcript_test",
      doctor_note_schema_id: "doctor_note_schema_test",
      note_type: "SOAP",
      note: {
        content: "Single-field fallback",
        Subjective: "Reported concern",
        Objective: "Observed finding",
        Assessment: "Assessment summary",
        Plan: "Follow-up plan",
      },
    }

    render(
      <VisitNoteViewer
        note={note}
        defaultOpen
        transcript={{
          id: "ai_visit_transcript_test",
          doctor_id: "doctor_test",
          patient_id: "patient_test",
          appointment_id: "appointment_test",
          duration: 1,
          created_at: new Date().toISOString(),
          transcript_data: [
            {
              speaker: "Synthetic speaker",
              content: "Synthetic transcript excluded from note copy",
              startTime: 0,
              endTime: 1,
            },
          ],
        }}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Visit Transcript" }))
    expect(
      screen.getByText("Synthetic transcript excluded from note copy"),
    ).toBeVisible()

    fireEvent.click(screen.getByRole("button", { name: "Copy to Clipboard" }))

    await screen.findByRole("button", { name: "Copied" })

    expect(
      screen.queryByRole("button", { name: "Billing header" }),
    ).not.toBeInTheDocument()

    const [clipboardItem] = await navigator.clipboard.read()
    const plainText = await (await clipboardItem.getType("text/plain")).text()

    expect(plainText).toContain("Single-field fallback")
    expect(plainText).toContain("Subjective")
    expect(plainText).toContain("Reported concern")
    expect(plainText).toContain("Objective")
    expect(plainText).toContain("Observed finding")
    expect(plainText).toContain("Assessment")
    expect(plainText).toContain("Assessment summary")
    expect(plainText).toContain("Plan")
    expect(plainText).toContain("Follow-up plan")
    expect(plainText).not.toBe(note.note.content)
    expect(plainText).not.toContain(
      "Synthetic transcript excluded from note copy",
    )
    const html = await (await clipboardItem.getType("text/html")).text()
    expect(html).not.toContain("<button")
    const order = ["content", "Subjective", "Objective", "Assessment", "Plan"]
    const positions = order.map((title) => html.indexOf(`<h3>${title}</h3>`))
    expect(positions.every((position) => position >= 0)).toBe(true)
    expect([...positions].sort((a, b) => a - b)).toEqual(positions)
  })

  it("copies a single section with its heading", async () => {
    render(
      <VisitNoteViewer
        defaultOpen
        note={{
          id: "visit_note_test",
          doctor_id: "doctor_test",
          appointment_id: "appointment_test",
          ai_transcript_id: "ai_visit_transcript_test",
          doctor_note_schema_id: "doctor_note_schema_test",
          note_type: "SOAP",
          note: { Subjective: "Reported concern", Plan: "Follow-up plan" },
        }}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Copy Plan" }))
    await screen.findByText("Copied to clipboard")

    const [clipboardItem] = await navigator.clipboard.read()
    const plainText = await (await clipboardItem.getType("text/plain")).text()
    expect(plainText).toContain("Plan")
    expect(plainText).toContain("Follow-up plan")
    expect(plainText).not.toContain("Reported concern")
    const html = await (await clipboardItem.getType("text/html")).text()
    expect(html).toMatch(/^<h3>Plan<\/h3><p>Follow-up plan<\/p>/)
  })
})

describe("per-note Session Prep review state", () => {
  const digest = "a".repeat(64)
  const note = (id: string): AiVisitNote => ({
    id,
    doctor_id: "doctor_test",
    appointment_id: "appointment_test",
    ai_transcript_id: "ai_visit_transcript_test",
    doctor_note_schema_id: "doctor_note_schema_test",
    note_type: id,
    note: { Plan: "Synthetic recorded plan" },
  })

  it("keeps mixed review states visible at collapsed note headings without inventing missing metadata", () => {
    render(
      <>
        <VisitNoteViewer
          note={note("reviewed")}
          defaultOpen={false}
          review={{
            content_sha256: digest,
            reviewed_at: "2026-10-31T12:00:00Z",
          }}
        />
        <VisitNoteViewer
          note={note("not_reviewed")}
          defaultOpen={false}
          review={{ content_sha256: digest, reviewed_at: null }}
        />
        <VisitNoteViewer note={note("unknown")} defaultOpen={false} />
      </>,
    )
    const reviewed = document.getElementById("note-reviewed")!
    const notReviewed = document.getElementById("note-not_reviewed")!
    const unknown = document.getElementById("note-unknown")!
    for (const element of [reviewed, notReviewed, unknown]) {
      expect(element).toHaveAttribute("data-state", "closed")
      expect(
        within(element).queryByText("Synthetic recorded plan"),
      ).not.toBeInTheDocument()
    }
    expect(
      within(reviewed).getByText("Reviewed for Session Prep"),
    ).toBeVisible()
    expect(
      within(notReviewed).getByText("Not reviewed for Session Prep"),
    ).toBeVisible()
    expect(
      within(unknown).queryByText(/reviewed for Session Prep/i),
    ).not.toBeInTheDocument()
    expect(
      within(unknown).queryByRole("button", {
        name: "Mark reviewed for Session Prep",
      }),
    ).not.toBeInTheDocument()
  })

  it("updates the heading only after the existing exact-digest review succeeds", async () => {
    vi.mocked(reviewNoteForSessionPrep).mockClear()
    vi.mocked(reviewNoteForSessionPrep).mockResolvedValue({
      data: { content_sha256: digest, reviewed_at: "2026-10-31T12:00:00Z" },
      response: new Response(null, { status: 200 }),
    } as never)
    render(
      <VisitNoteViewer
        note={note("current")}
        defaultOpen={false}
        review={{ content_sha256: digest, reviewed_at: null }}
      />,
    )
    expect(reviewNoteForSessionPrep).not.toHaveBeenCalled()
    fireEvent.click(
      screen.getByRole("button", { name: "Mark reviewed for Session Prep" }),
    )
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Reviewed for Session Prep",
    )
    expect(reviewNoteForSessionPrep).toHaveBeenCalledExactlyOnceWith({
      path: { note_id: "current" },
      body: { expected_content_sha256: digest },
      throwOnError: false,
    })
    expect(document.getElementById("note-current")).toHaveAttribute(
      "data-state",
      "closed",
    )
  })

  it("does not claim a review when the saved response has another digest", async () => {
    vi.mocked(reviewNoteForSessionPrep).mockResolvedValue({
      data: {
        content_sha256: "b".repeat(64),
        reviewed_at: "2026-10-31T12:00:00Z",
      },
      response: new Response(null, { status: 200 }),
    } as never)
    render(
      <VisitNoteViewer
        note={note("current")}
        defaultOpen={false}
        review={{ content_sha256: digest, reviewed_at: null }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Mark reviewed for Session Prep" }),
    )
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "could not be saved",
    )
    expect(screen.getByText("Not reviewed for Session Prep")).toBeVisible()
    expect(
      screen.queryByText("Reviewed for Session Prep"),
    ).not.toBeInTheDocument()
  })

  it("cannot transfer a late review response to a corrected note version", async () => {
    let finish: (value: never) => void = () => {}
    vi.mocked(reviewNoteForSessionPrep).mockReturnValue(
      new Promise<never>((resolve) => {
        finish = resolve
      }),
    )
    const view = render(
      <VisitNoteViewer
        note={note("current")}
        defaultOpen={false}
        review={{ content_sha256: digest, reviewed_at: null }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Mark reviewed for Session Prep" }),
    )
    view.rerender(
      <VisitNoteViewer
        note={{
          ...note("current"),
          note: { Plan: "Synthetic corrected plan" },
        }}
        defaultOpen={false}
        review={{ content_sha256: "b".repeat(64), reviewed_at: null }}
      />,
    )
    finish({
      data: { content_sha256: digest, reviewed_at: "2026-10-31T12:00:00Z" },
      response: new Response(null, { status: 200 }),
    } as never)
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Mark reviewed for Session Prep" }),
      ).toBeEnabled(),
    )
    expect(screen.getByText("Not reviewed for Session Prep")).toBeVisible()
    expect(
      screen.queryByText("Reviewed for Session Prep"),
    ).not.toBeInTheDocument()
  })
})
