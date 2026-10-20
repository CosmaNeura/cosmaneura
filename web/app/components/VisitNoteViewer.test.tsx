import { describe, expect, it, vi } from "vitest"

import type { AiVisitNote } from "~/configuration/client"

import VisitNoteViewer from "./VisitNoteViewer"
import { fireEvent, render, screen } from "@testing-library/react"

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
