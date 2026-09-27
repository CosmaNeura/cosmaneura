import { describe, expect, it } from "vitest"

import type { AiVisitNote } from "~/configuration/client"

import VisitNoteViewer from "./VisitNoteViewer"
import { fireEvent, render, screen } from "@testing-library/react"

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

    await screen.findByRole("button", { name: "Copied!" })

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
  })
})
