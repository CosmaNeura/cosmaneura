import type { PropsWithChildren } from "react"
import {
  RouterProvider,
  createMemoryRouter,
  useLoaderData,
  useRouteError,
} from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { NoteDetail } from "~/configuration/client"

import NoteDetailPage, { ErrorBoundary, clientLoader } from "./detail"
import { act, fireEvent, render, screen } from "@testing-library/react"

const { read } = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock("~/configuration/client", () => ({ noteDetail: read }))
vi.mock("@clerk/clerk-react", () => ({ useUser: () => ({ user: null }) }))
vi.mock("~/utils/terminology", () => ({
  useTerminology: () => ({ patient: "patient" }),
}))
vi.mock("~/utils/engagement-feature", () => ({
  isEngagementEnabled: () => false,
}))
vi.mock("~/utils/reference-toolkit-feature", () => ({
  isReferenceToolkitEnabled: () => false,
  isReferenceLabsEnabled: () => false,
}))
vi.mock("~/sovereign/SovereignPanelHost", () => ({
  SovereignPanelHost: () => null,
}))
vi.mock("@/components/notes/UploadRecordingButton", () => ({
  UploadRecordingButton: () => null,
}))
vi.mock("~/components/MedicationPanel", () => ({ MedicationPanel: () => null }))
vi.mock("~/components/LabResultsPanel", () => ({ LabResultsPanel: () => null }))
vi.mock("@/components/ui/sidebar", () => ({
  SidebarInset: ({ children }: PropsWithChildren) => <div>{children}</div>,
  SidebarTrigger: () => null,
}))
vi.mock("~/components/VisitTranscriptViewer", () => ({
  default: ({ transcript }: { transcript: { id: string } | null }) => (
    <div aria-label="transcript">{transcript?.id ?? "No transcript"}</div>
  ),
}))

function visit(id = "synthetic_visit", ready = false): NoteDetail {
  return {
    appointment: {
      id,
      appointment_date: "2026-09-30",
      doctor_id: "synthetic_doctor",
      patient_id: "synthetic_patient",
      external_id: null,
      patient_name: "Synthetic Patient",
      intake_status: "none",
    },
    patient: {
      id: "synthetic_patient",
      name: "Synthetic Patient",
      doctor_id: "synthetic_doctor",
      date_of_birth: "2001-01-01",
    },
    capture_workflow_required: true,
    transcript: {
      id: `source_${id}`,
      doctor_id: "synthetic_doctor",
      patient_id: "synthetic_patient",
      appointment_id: id,
      duration: 1,
      transcript_data: [],
    },
    notes: ready
      ? [
          {
            id: `note_${id}`,
            doctor_id: "synthetic_doctor",
            appointment_id: id,
            doctor_note_schema_id: "synthetic_schema",
            ai_transcript_id: `source_${id}`,
            note_type: "SOAP",
            note: { Plan: `Prepared draft for ${id}` },
          },
        ]
      : [],
  }
}

const response = (data: NoteDetail) => ({
  data,
  response: new Response(null, { status: 200 }),
})

function LoadedVisit() {
  const loaderData = useLoaderData()
  return (
    <NoteDetailPage
      {...({ loaderData } as Parameters<typeof NoteDetailPage>[0])}
    />
  )
}

function Boundary() {
  return (
    <ErrorBoundary
      {...({ error: useRouteError() } as Parameters<typeof ErrorBoundary>[0])}
    />
  )
}

async function openVisit() {
  const router = createMemoryRouter(
    [
      { path: "/notes", element: <div>Choose a visit</div> },
      {
        path: "/notes/:id",
        loader: (args) =>
          clientLoader(args as Parameters<typeof clientLoader>[0]),
        element: <LoadedVisit />,
        errorElement: <Boundary />,
        hydrateFallbackElement: <div>Loading visit</div>,
      },
    ],
    { initialEntries: ["/notes/synthetic_visit"] },
  )
  let rendered!: ReturnType<typeof render>
  await act(async () => {
    rendered = render(<RouterProvider router={router} />)
  })
  return { router, ...rendered }
}

const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })

beforeEach(() => {
  read.mockReset()
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] })
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe("prepared Scribe note readiness through the actual page and router", () => {
  it("shows a newly published draft without reload and stops all automatic reads", async () => {
    read.mockResolvedValueOnce(response(visit()))
    read.mockResolvedValue(response(visit("synthetic_visit", true)))
    await openVisit()
    expect(screen.getByText("Checking for a prepared note…")).toBeVisible()
    expect(screen.getByLabelText("transcript")).toHaveTextContent(
      "source_synthetic_visit",
    )
    await advance(3_000)
    expect(screen.getByText("Prepared draft for synthetic_visit")).toBeVisible()
    expect(screen.queryByRole("button", { name: /Check/ })).toBeNull()
    expect(read).toHaveBeenCalledTimes(2)
    await advance(120_000)
    fireEvent.focus(window)
    document.dispatchEvent(new Event("visibilitychange"))
    expect(read).toHaveBeenCalledTimes(2)
    expect(read).toHaveBeenLastCalledWith(
      expect.objectContaining({
        throwOnError: false,
        path: { appointment_id: "synthetic_visit" },
        signal: expect.any(AbortSignal),
      }),
    )
  })

  it("pauses when hidden, checks on return, and never overlaps pending reads", async () => {
    read.mockResolvedValueOnce(response(visit()))
    let finish!: (value: unknown) => void
    read.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)))
    read.mockResolvedValue(response(visit()))
    await openVisit()
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden")
    fireEvent(document, new Event("visibilitychange"))
    await advance(12_000)
    fireEvent.focus(window)
    expect(read).toHaveBeenCalledTimes(1)
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
    fireEvent(document, new Event("visibilitychange"))
    expect(read).toHaveBeenCalledTimes(2)
    fireEvent.focus(window)
    fireEvent.click(screen.getByRole("button", { name: "Checking…" }))
    await advance(9_000)
    expect(read).toHaveBeenCalledTimes(2)
    await act(async () => finish(response(visit())))
    await advance(3_000)
    expect(read).toHaveBeenCalledTimes(3)
  })

  it("ends the automatic window and permits only an explicit read after it", async () => {
    read.mockResolvedValue(response(visit()))
    await openVisit()
    await advance(90_000)
    expect(
      screen.getByText(/A prepared note hasn’t appeared yet/),
    ).toBeVisible()
    const count = read.mock.calls.length
    expect(count).toBe(30)
    await advance(180_000)
    fireEvent.focus(window)
    fireEvent(document, new Event("visibilitychange"))
    expect(read).toHaveBeenCalledTimes(count)
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Check for note" }))
    })
    expect(read).toHaveBeenCalledTimes(count + 1)
    await advance(90_000)
    expect(read).toHaveBeenCalledTimes(count + 1)
  })

  it("removes timers and listeners when the page unmounts", async () => {
    read.mockResolvedValue(response(visit()))
    const { unmount, router } = await openVisit()
    unmount()
    router.dispose()
    await advance(120_000)
    fireEvent.focus(window)
    fireEvent(document, new Event("visibilitychange"))
    expect(read).toHaveBeenCalledTimes(1)
  })

  it("aborts the old read on navigation and ignores its late response", async () => {
    read.mockResolvedValueOnce(response(visit()))
    let finish!: (value: unknown) => void
    read.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)))
    read.mockResolvedValue(response(visit("next_visit", true)))
    const { router } = await openVisit()
    await advance(3_000)
    const oldSignal = read.mock.calls[1][0].signal as AbortSignal
    await act(async () => {
      await router.navigate("/notes/next_visit")
    })
    expect(oldSignal.aborted).toBe(true)
    await act(async () => finish(response(visit("synthetic_visit", true))))
    expect(screen.getByText("Prepared draft for next_visit")).toBeVisible()
    expect(screen.queryByText("Prepared draft for synthetic_visit")).toBeNull()
    await advance(120_000)
    expect(read).toHaveBeenCalledTimes(3)
  })

  it.each([
    [401, "Your session needs verification"],
    [403, "Visit unavailable"],
    [503, "Couldn’t load this visit"],
  ])(
    "drops the previous view and stops checking on HTTP %i",
    async (status, title) => {
      read.mockResolvedValueOnce(response(visit()))
      read.mockResolvedValue({ response: new Response(null, { status }) })
      await openVisit()
      await advance(3_000)
      expect(screen.getByRole("heading", { name: title })).toBeVisible()
      expect(screen.queryByLabelText("transcript")).toBeNull()
      await advance(120_000)
      fireEvent.focus(window)
      expect(read).toHaveBeenCalledTimes(2)
    },
  )

  it.each(["existing note", "no transcript", "non-capture"])(
    "does not poll a page with %s",
    async (condition) => {
      const value = visit("synthetic_visit", condition === "existing note")
      if (condition === "no transcript") value.transcript = null
      if (condition === "non-capture") value.capture_workflow_required = false
      read.mockResolvedValue(response(value))
      await openVisit()
      await advance(120_000)
      fireEvent.focus(window)
      expect(read).toHaveBeenCalledTimes(1)
      expect(screen.queryByText("Checking for a prepared note…")).toBeNull()
    },
  )
})
