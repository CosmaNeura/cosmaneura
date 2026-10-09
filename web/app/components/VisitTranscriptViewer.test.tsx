import { describe, expect, it } from "vitest"

import type { AiVisitTranscript } from "~/configuration/client"

import VisitTranscriptViewer, {
  transcriptExpiry,
} from "./VisitTranscriptViewer"
import { fireEvent, render, screen } from "@testing-library/react"

function transcript(overrides: Partial<AiVisitTranscript> = {}) {
  return {
    id: "ai_visit_transcript_test",
    doctor_id: "doctor_test",
    patient_id: "patient_test",
    appointment_id: "appointment_test",
    duration: 60,
    created_at: new Date().toISOString(),
    transcript_data: [
      {
        speaker: "Synthetic speaker",
        content: "Synthetic transcript line",
        startTime: 0,
        endTime: 1,
      },
    ],
    ...overrides,
  } as AiVisitTranscript
}

describe("VisitTranscriptViewer", () => {
  it("survives switching between visits with and without a transcript", () => {
    const { rerender } = render(<VisitTranscriptViewer transcript={null} />)
    expect(screen.getByText("No Transcript Available.")).toBeInTheDocument()

    rerender(<VisitTranscriptViewer transcript={transcript()} />)
    expect(
      screen.getByRole("button", { name: "Visit Transcript" }),
    ).toBeInTheDocument()

    rerender(<VisitTranscriptViewer transcript={null} />)
    expect(screen.getByText("No Transcript Available.")).toBeInTheDocument()
  })

  it("starts collapsed and shows the date the transcript is kept until", () => {
    const createdAt = new Date().toISOString()
    const until = transcriptExpiry(createdAt).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    })
    render(
      <VisitTranscriptViewer
        transcript={transcript({ created_at: createdAt })}
      />,
    )
    expect(screen.queryByText("Synthetic transcript line")).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Visit Transcript" }))

    expect(screen.getByText("Synthetic transcript line")).toBeVisible()
    expect(screen.getByText(`Available until ${until}.`)).toBeInTheDocument()
  })

  it("keeps transcripts for the backend's 14-day cleanup window", () => {
    expect(transcriptExpiry("2026-10-08T15:00:00Z").toISOString()).toBe(
      "2026-10-22T15:00:00.000Z",
    )
  })

  it("explains a deleted transcript", () => {
    render(
      <VisitTranscriptViewer
        transcript={transcript({ deleted_at: new Date().toISOString() })}
      />,
    )

    expect(
      screen.getByText(
        "This transcript has been deleted. Transcripts are kept for 14 days.",
      ),
    ).toBeInTheDocument()
  })

  it("treats a transcript past retention as deleted", () => {
    render(
      <VisitTranscriptViewer
        transcript={transcript({ created_at: "2020-01-01T00:00:00Z" })}
      />,
    )

    expect(screen.getByText(/has been deleted/)).toBeInTheDocument()
  })
})
