import type { PropsWithChildren } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { AiVisitTranscript, NoteDetail } from "~/configuration/client"
import {
  markProcessing,
  resetRecentUploadsForTests,
  useRecentUploads,
} from "~/lib/recent-uploads"
import {
  isReferenceLabsEnabled,
  isReferenceToolkitEnabled,
} from "~/utils/reference-toolkit-feature"

import NoteDetailPage from "./detail"
import {
  fireEvent,
  render,
  renderHook,
  screen,
  within,
} from "@testing-library/react"

vi.mock("~/configuration/client", () => ({ noteDetail: vi.fn() }))
const toast = vi.hoisted(() => vi.fn())
vi.mock("~/hooks/use-toast", () => ({ toast }))
vi.mock("@clerk/clerk-react", () => ({ useUser: () => ({ user: null }) }))
vi.mock("~/utils/terminology", () => ({
  useTerminology: () => ({ patient: "patient" }),
}))
vi.mock("~/utils/engagement-feature", () => ({
  isEngagementEnabled: () => false,
}))
vi.mock("~/utils/reference-toolkit-feature", () => ({
  isReferenceToolkitEnabled: vi.fn(() => false),
  isReferenceLabsEnabled: vi.fn(() => false),
}))
const sessionPrepUi = vi.hoisted(() => ({
  buildAvailable: false,
  settings: undefined as
    | {
        session_prep?: {
          eligible: boolean
          can_review_sources: boolean
        }
      }
    | undefined,
}))
vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...actual,
    useQuery: () => ({ data: sessionPrepUi.settings }),
  }
})
vi.mock("client/@tanstack/react-query.gen", () => ({
  settingsDataOptions: () => ({ queryKey: ["settings"] }),
}))
vi.mock("~/sovereign/SovereignPanelHost", () => ({
  SovereignPanelHost: () => <div data-testid="session-prep-panel" />,
  resolveSovereignProviderConfig: () =>
    sessionPrepUi.buildAvailable
      ? { origin: "https://sovereign.invalid" }
      : null,
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
vi.mock("~/components/MedicationPanel", () => ({
  MedicationPanel: () => <div>Medication tools</div>,
}))
vi.mock("~/components/SessionPrepPriorNotes", () => ({
  SessionPrepPriorNotes: () => <div>Prior visits for Session Prep</div>,
}))
vi.mock("~/components/LabResultsPanel", () => ({
  LabResultsPanel: () => <div>Lab tools</div>,
}))

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

beforeEach(() => {
  sessionPrepUi.buildAvailable = false
  sessionPrepUi.settings = undefined
})

const priorNotes: NonNullable<NoteDetail["session_prep_prior_notes"]> = {
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
  ],
}

describe("Session Prep provider discovery", () => {
  it("shows the panel and prior-visit controls to a selected provider with current access", () => {
    sessionPrepUi.buildAvailable = true
    sessionPrepUi.settings = {
      session_prep: { eligible: true, can_review_sources: true },
    }

    show({ priorNotes })

    expect(screen.getByTestId("session-prep-panel")).toBeInTheDocument()
    expect(screen.getByText("Prior visits for Session Prep")).toBeVisible()
  })

  it.each([
    [
      "an existing provider outside the selected cohort",
      { session_prep: { eligible: false, can_review_sources: false } },
    ],
    ["a newly onboarded provider without discovery state", {}],
    [
      "a selected provider without current practice entitlement",
      { session_prep: { eligible: true, can_review_sources: false } },
    ],
  ])("hides every launcher for %s", (_label, settings) => {
    sessionPrepUi.buildAvailable = true
    sessionPrepUi.settings = settings

    show({ priorNotes })

    expect(screen.queryByTestId("session-prep-panel")).not.toBeInTheDocument()
    expect(
      screen.queryByText("Prior visits for Session Prep"),
    ).not.toBeInTheDocument()
  })
})

describe("note detail page heading", () => {
  it("renders the patient name as the only page-level heading", () => {
    show()
    const headings = screen.getAllByRole("heading", { level: 1 })
    expect(headings).toHaveLength(1)
    expect(headings[0]).toHaveTextContent("Synthetic Patient")
  })
})

describe("a note arriving for a just-uploaded visit", () => {
  afterEach(() => {
    resetRecentUploadsForTests()
    toast.mockReset()
  })

  it("is announced and stops being tracked", () => {
    markProcessing("appointment_fixture")
    show()

    const { result } = renderHook(() => useRecentUploads())
    expect(result.current.startedAt.has("appointment_fixture")).toBe(false)
    expect(result.current.justReady.has("appointment_fixture")).toBe(true)
    expect(toast).toHaveBeenCalledOnce()
    expect(toast).toHaveBeenCalledWith({ title: "Your note is ready" })
  })

  it("says plainly when a visit has no note", () => {
    show({ notes: [], transcript: null })
    expect(screen.getByText("No note for this visit yet.")).toBeVisible()
  })
})

describe("note transcript provenance", () => {
  afterEach(() => vi.restoreAllMocks())
  it("uses one page heading and a nested heading for note content", () => {
    show({ notes: [] })
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
    expect(
      screen.getByRole("heading", { level: 1, name: "Synthetic Patient" }),
    ).toBeVisible()
    expect(
      screen.getByRole("heading", { level: 2, name: "Notes" }),
    ).toBeVisible()
  })

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
    show({ notes: [], captureRequired: true, transcript: null })
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

it("keeps medications available while the independent lab release gate is off", () => {
  vi.mocked(isReferenceToolkitEnabled).mockReturnValueOnce(true)
  vi.mocked(isReferenceLabsEnabled).mockReturnValueOnce(false)
  show()
  expect(screen.getByText("Medication tools")).toBeVisible()
  expect(screen.queryByText("Lab tools")).not.toBeInTheDocument()
})

it("shows labs only when the independent lab gate permits them", () => {
  vi.mocked(isReferenceToolkitEnabled).mockReturnValueOnce(true)
  vi.mocked(isReferenceLabsEnabled).mockReturnValueOnce(true)
  show()
  expect(screen.getByText("Lab tools")).toBeVisible()
})
