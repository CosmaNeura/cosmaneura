import { RouterProvider, createMemoryRouter } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { SidebarEntry } from "@/components/app-sidebar"
import { withUploadProgress } from "~/layouts/notes"
import {
  markProcessing,
  resetRecentUploadsForTests,
  useRecentUploads,
} from "~/lib/recent-uploads"

import {
  NOTE_PROCESSING_POLL_MS,
  NOTE_PROCESSING_TIMEOUT_MS,
  NoteProcessing,
} from "./detail"
import { act, render, renderHook, screen } from "@testing-library/react"

vi.mock("~/configuration/client", () => ({
  noteDetail: vi.fn(),
  noteList: vi.fn(),
}))

afterEach(() => {
  vi.useRealTimers()
  resetRecentUploadsForTests()
})

describe("note processing state", () => {
  it("re-checks while open and gives up after the timeout", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const loader = vi.fn(() => null)
    markProcessing("appointment_new")
    const router = createMemoryRouter(
      [
        {
          path: "/",
          loader,
          element: <NoteProcessing appointmentId="appointment_new" />,
        },
      ],
      { initialEntries: ["/"] },
    )
    render(<RouterProvider router={router} />)

    expect(await screen.findByText("Writing your note…")).toBeVisible()
    const initialCalls = loader.mock.calls.length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_PROCESSING_POLL_MS)
    })
    expect(loader.mock.calls.length).toBeGreaterThan(initialCalls)

    const { result } = renderHook(() => useRecentUploads())
    expect(result.current.processing.has("appointment_new")).toBe(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_PROCESSING_TIMEOUT_MS)
    })
    expect(result.current.processing.has("appointment_new")).toBe(false)
  })
})

describe("sidebar upload progress", () => {
  const entry: SidebarEntry = {
    id: "appointment_new",
    url: "/notes/appointment_new",
    title: "Synthetic Person",
    location: "",
    date: "Today",
    isoDate: new Date().toISOString(),
    status: "not-started",
    statusLabel: "No note yet",
    preview: "",
  }

  it("shows a just-uploaded visit as being written", () => {
    const result = withUploadProgress(entry, {
      processing: new Set(["appointment_new"]),
      justReady: new Set(),
    })
    expect(result).toMatchObject({
      status: "in-progress",
      statusLabel: "Writing note",
    })
  })

  it("highlights a visit whose note just arrived", () => {
    const result = withUploadProgress(
      { ...entry, status: "completed", statusLabel: "Note ready" },
      { processing: new Set(), justReady: new Set(["appointment_new"]) },
    )
    expect(result.highlight).toBe(true)
    expect(result.status).toBe("completed")
  })
})
