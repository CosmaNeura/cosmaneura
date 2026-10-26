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

  it("explains a deleted transcript without blaming retention", () => {
    render(
      <VisitTranscriptViewer
        transcript={transcript({ deleted_at: new Date().toISOString() })}
      />,
    )

    expect(
      screen.getByText("This transcript has been deleted."),
    ).toBeInTheDocument()
  })

  it("states the retention rule when the creation time is unknown", () => {
    render(
      <VisitTranscriptViewer
        transcript={transcript({ created_at: undefined })}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Visit Transcript" }))

    expect(
      screen.getByText(
        "Transcripts are deleted 14 days after they are created.",
      ),
    ).toBeInTheDocument()
  })

  it("treats a transcript past retention as deleted", () => {
    render(
      <VisitTranscriptViewer
        transcript={transcript({ created_at: "2020-01-01T00:00:00Z" })}
      />,
    )

    expect(
      screen.getByText(
        "This transcript has been deleted. Transcripts are kept for 14 days.",
      ),
    ).toBeInTheDocument()
  })
})

describe("acoustic speaker display", () => {
  const first = "c6019b7d-a888-5c40-8374-385a6110dfbd"
  const second = "50a29b59-eb47-53b4-8e47-e553c34506fb"
  const third = "768d529f-0993-5125-91bc-9624c3fc2607"
  const turn = (
    id: string | undefined,
    content: string,
    speaker = "Unknown",
  ) => ({
    source_speaker_id: id,
    speaker,
    content,
    startTime: 0,
    endTime: 1000,
  })

  it("numbers 0,1,0,2 as 1,2,1,3 and keeps absent identity and overlapping starts visible", () => {
    render(
      <VisitTranscriptViewer
        transcript={transcript({
          transcript_data: [
            turn(first, "First synthetic turn"),
            turn(second, "Second synthetic turn"),
            turn(first, "Third synthetic turn"),
            turn(third, "Fourth synthetic turn"),
            turn(undefined, "Legacy synthetic turn"),
          ],
        })}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Visit Transcript" }))
    expect(screen.getAllByText("Speaker 1 (role unknown):")).toHaveLength(2)
    expect(screen.getAllByText("Speaker 2 (role unknown):")).toHaveLength(1)
    expect(screen.getAllByText("Speaker 3 (role unknown):")).toHaveLength(1)
    expect(screen.getByText("Unknown:")).toBeVisible()
    for (const text of [
      "First synthetic turn",
      "Second synthetic turn",
      "Third synthetic turn",
      "Fourth synthetic turn",
      "Legacy synthetic turn",
    ]) {
      expect(screen.getByText(text)).toBeVisible()
    }
    expect(
      screen.getByText(
        /Speaker numbers distinguish voices within this transcript/,
      ),
    ).toBeVisible()
    expect(screen.queryByText(first)).toBeNull()
    expect(screen.queryByText(second)).toBeNull()
    expect(screen.queryByText(third)).toBeNull()
    expect(screen.queryByText(/Patient:/)).toBeNull()
  })

  it("keeps the supplied role separate and resets numbers for another transcript", () => {
    const { rerender } = render(
      <VisitTranscriptViewer
        transcript={transcript({
          transcript_data: [
            turn(first, "Synthetic role-labeled turn", "Doctor"),
            turn(second, "Synthetic unknown turn"),
          ],
        })}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Visit Transcript" }))
    expect(screen.getByText("Speaker 1 (Doctor):")).toBeVisible()
    expect(screen.getByText("Speaker 2 (role unknown):")).toBeVisible()
    rerender(
      <VisitTranscriptViewer
        transcript={transcript({
          transcript_data: [turn(second, "Another recording turn")],
        })}
      />,
    )
    expect(screen.getByText("Speaker 1 (role unknown):")).toBeVisible()
    expect(screen.queryByText("Speaker 2 (role unknown):")).toBeNull()
  })
})
