import type { PropsWithChildren } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { AiVisitTranscript, NoteDetail } from "~/configuration/client"

import NoteDetailPage from "./detail"
import { fireEvent, render, screen, within } from "@testing-library/react"

vi.mock("~/configuration/client", () => ({ noteDetail: vi.fn() }))
vi.mock("@clerk/clerk-react", () => ({ useUser: () => ({ user: null }) }))
vi.mock("~/utils/terminology", () => ({
  useTerminology: () => ({ patient: "patient" }),
}))
vi.mock("~/utils/engagement-feature", () => ({
  isEngagementEnabled: () => false,
}))
vi.mock("~/utils/reference-toolkit-feature", () => ({
  isReferenceToolkitEnabled: () => false,
}))
vi.mock("~/sovereign/SovereignPanelHost", () => ({
  SovereignPanelHost: () => null,
}))
vi.mock("@/components/notes/UploadRecordingButton", () => ({
  UploadRecordingButton: () => null,
}))
vi.mock("@/components/ui/separator", () => ({ Separator: () => null }))
vi.mock("@/components/ui/sidebar", () => ({
  SidebarInset: ({ children }: PropsWithChildren) => <div>{children}</div>,
  SidebarTrigger: () => null,
}))
vi.mock("~/components/shared/InnerScrollableContent", () => ({
  InnerScrollableContent: ({ children }: PropsWithChildren) => (
    <div>{children}</div>
  ),
}))
vi.mock("~/components/shared/StatusBadge", () => ({ StatusBadge: () => null }))
vi.mock("~/components/shared/EngagementBadge", () => ({
  EngagementBadge: () => null,
}))
vi.mock("~/components/MedicationPanel", () => ({ MedicationPanel: () => null }))
vi.mock("~/components/LabResultsPanel", () => ({ LabResultsPanel: () => null }))

// Keep the real note component/collapsible/copy behavior. Replace the transcript
// renderer only, so these tests focus on which source the page supplies to it.
vi.mock("~/components/VisitTranscriptViewer", () => ({
  default: ({ transcript }: { transcript: { id: string } | null }) => (
    <div aria-label="source-transcript">{transcript?.id ?? "unavailable"}</div>
  ),
}))

const note = (id: string, source: string, kind = "SOAP") => ({
  id,
  doctor_id: "doctor_fixture",
  appointment_id: "appointment_fixture",
  doctor_note_schema_id: `schema_${id}`,
  ai_transcript_id: source,
  note_type: kind,
  note: { Plan: `Synthetic ${id} note` },
})

function show({
  notes = [note("soap", "source_a")],
  transcript = { id: "source_a" } as { id: string } | null,
  captureRequired = false,
  sources = undefined as { id: string }[] | undefined,
  focusedNoteId = null as string | null,
  priorNotes = undefined as NoteDetail["session_prep_prior_notes"],
} = {}) {
  const source = ({ id }: { id: string }): AiVisitTranscript => ({
    id,
    doctor_id: "doctor_fixture",
    patient_id: "patient_fixture",
    appointment_id: "appointment_fixture",
    duration: 1,
    transcript_data: [],
  })
  const loaderData: NoteDetail = {
    appointment: {
      id: "appointment_fixture",
      appointment_date: "2026-09-22",
      doctor_id: "doctor_fixture",
      patient_id: "patient_fixture",
      external_id: null,
      patient_name: "Synthetic Patient",
      intake_status: "none",
    },
    patient: {
      id: "patient_fixture",
      name: "Synthetic Patient",
      doctor_id: "doctor_fixture",
      date_of_birth: "2001-01-01",
    },
    notes,
    session_prep_prior_notes: priorNotes,
    capture_workflow_required: captureRequired,
    transcript: transcript ? source(transcript) : null,
    ...(sources === undefined ? {} : { note_transcripts: sources.map(source) }),
  }
  return render(
    <NoteDetailPage
      {...({ loaderData: { ...loaderData, focusedNoteId } } as Parameters<
        typeof NoteDetailPage
      >[0])}
    />,
  )
}

describe("note transcript provenance", () => {
  afterEach(() => vi.restoreAllMocks())
  it("opens the exact note requested from the prior-visit review list", () => {
    const scroll = vi
      .spyOn(HTMLElement.prototype, "scrollIntoView")
      .mockImplementation(() => {})
    show({
      notes: [note("soap", "source_a"), note("summary", "source_a", "Summary")],
      focusedNoteId: "summary",
      priorNotes: {
        notes: [],
        included_count: 0,
        more_notes: false,
        selection_limited: false,
      },
    })
    expect(screen.getByText("Synthetic summary note")).toBeVisible()
    expect(document.getElementById("note-summary")).toHaveAttribute(
      "data-state",
      "open",
    )
    expect(document.getElementById("note-soap")).toHaveAttribute(
      "data-state",
      "closed",
    )
    expect(
      screen.queryByText("Prior visits for Session Prep"),
    ).not.toBeInTheDocument()
    expect(scroll).toHaveBeenCalledExactlyOnceWith({ block: "start" })
    expect(scroll.mock.instances[0]).toHaveAttribute("id", "note-summary")
  })
  it("explains how to resume a capture with no generated note", () => {
    show({ notes: [], captureRequired: true })
    expect(screen.getByText(/Reopen it in Scribe/)).toHaveTextContent(
      "Use a new visit for a separate upload",
    )
    expect(
      screen.queryByText("No Notes Available. Upload some!"),
    ).not.toBeInTheDocument()
  })

  it("keeps one shared transcript when current notes share a source", () => {
    show({
      notes: [note("soap", "source_a"), note("summary", "source_a", "Summary")],
      sources: [{ id: "source_a" }],
    })
    expect(screen.getAllByLabelText("source-transcript")).toHaveLength(1)
    expect(screen.getByLabelText("source-transcript")).toHaveTextContent(
      "source_a",
    )
  })

  it("pairs different current notes with their own sources", () => {
    show({
      notes: [
        note("soap", "source_old"),
        note("summary", "source_reviewed", "Summary"),
      ],
      transcript: null,
      sources: [{ id: "source_reviewed" }, { id: "source_old" }],
    })
    expect(screen.getByLabelText("source-transcript")).toHaveTextContent(
      "source_old",
    )
    fireEvent.click(screen.getByRole("button", { name: "Summary" }))
    const sourceViews = screen.getAllByLabelText("source-transcript")
    expect(sourceViews.map((view) => view.textContent)).toEqual([
      "source_old",
      "source_reviewed",
    ])
    const summary = screen
      .getByText("Synthetic summary note")
      .closest("div[data-state='open']")!
    expect(
      within(summary as HTMLElement).getByLabelText("source-transcript"),
    ).toHaveTextContent("source_reviewed")
  })

  it("does not substitute another note's source when one is missing", () => {
    show({
      notes: [
        note("soap", "source_missing"),
        note("summary", "source_reviewed", "Summary"),
      ],
      transcript: null,
      sources: [{ id: "source_reviewed" }],
    })
    expect(screen.getByLabelText("source-transcript")).toHaveTextContent(
      "unavailable",
    )
  })

  it("accepts a prior backend's shared transcript only for matching note IDs", () => {
    show({ transcript: { id: "unrelated" } })
    expect(screen.getByLabelText("source-transcript")).toHaveTextContent(
      "unavailable",
    )
  })

  it("supports a prior backend's single matching source", () => {
    show()
    expect(screen.getByLabelText("source-transcript")).toHaveTextContent(
      "source_a",
    )
  })
})
