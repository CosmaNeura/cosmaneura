import { RouterProvider, createMemoryRouter } from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { SidebarEntry } from "@/components/app-sidebar"
import { withUploadProgress } from "~/layouts/notes"
import {
  NOTE_PROCESSING_TIMEOUT_MS,
  isProcessing,
  markProcessing,
  resetRecentUploadsForTests,
  useRecentUploads,
} from "~/lib/recent-uploads"

import { NOTE_PROCESSING_POLL_MS, NoteProcessing } from "./detail"
import { act, render, renderHook, screen } from "@testing-library/react"

const client = vi.hoisted(() => ({ noteDetail: vi.fn(), noteList: vi.fn() }))
vi.mock("~/configuration/client", () => client)

beforeEach(() => {
  client.noteDetail.mockReset()
  client.noteDetail.mockResolvedValue({ data: { notes: [] } })
})

afterEach(() => {
  vi.useRealTimers()
  act(() => resetRecentUploadsForTests())
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: false,
  })
})

function renderProcessing(loader: () => unknown = vi.fn(() => null)) {
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
  const view = render(<RouterProvider router={router} />)
  return { loader, ...view }
}

describe("note processing state", () => {
  it("checks the visit record and refreshes the page only once a note exists", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    markProcessing("appointment_new")
    const { loader } = renderProcessing()
    expect(await screen.findByText("Writing your note…")).toBeVisible()
    const initialLoads = vi.mocked(loader).mock.calls.length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_PROCESSING_POLL_MS)
    })
    expect(client.noteDetail).toHaveBeenCalledWith(
      expect.objectContaining({
        throwOnError: false,
        path: { appointment_id: "appointment_new" },
        signal: expect.any(AbortSignal),
      }),
    )
    expect(vi.mocked(loader).mock.calls.length).toBe(initialLoads)

    client.noteDetail.mockResolvedValue({ data: { notes: [{ id: "n" }] } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_PROCESSING_POLL_MS)
    })
    expect(vi.mocked(loader).mock.calls.length).toBeGreaterThan(initialLoads)
  })

  it("does not poll while the tab is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    markProcessing("appointment_new")
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    })
    renderProcessing()
    await screen.findByText("Writing your note…")

    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_PROCESSING_POLL_MS * 2)
    })
    expect(client.noteDetail).not.toHaveBeenCalled()
  })

  it("recovers on the next poll after an SDK rejection", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    client.noteDetail.mockRejectedValueOnce(
      new Error("Synthetic authentication failure"),
    )
    const { loader } = renderProcessing()
    await screen.findByText("Writing your note…")
    const initialLoads = vi.mocked(loader).mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_PROCESSING_POLL_MS)
    })
    client.noteDetail.mockResolvedValue({ data: { notes: [{ id: "n" }] } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_PROCESSING_POLL_MS)
    })
    expect(client.noteDetail).toHaveBeenCalledTimes(2)
    expect(vi.mocked(loader).mock.calls.length).toBeGreaterThan(initialLoads)
  })

  it("bounds a reader that ignores abort so later polls can run", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    client.noteDetail.mockReturnValueOnce(new Promise(() => {}))
    renderProcessing()
    await screen.findByText("Writing your note…")
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_PROCESSING_POLL_MS * 3)
    })
    expect(client.noteDetail.mock.calls.length).toBeGreaterThan(1)
    expect(client.noteDetail.mock.calls[0][0].signal.aborted).toBe(true)
  })

  it("aborts on unmount and ignores a late ready response", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let finish!: (value: unknown) => void
    client.noteDetail.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    const { loader, unmount } = renderProcessing()
    await screen.findByText("Writing your note…")
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTE_PROCESSING_POLL_MS)
    })
    const initialLoads = vi.mocked(loader).mock.calls.length
    unmount()
    expect(client.noteDetail.mock.calls[0][0].signal.aborted).toBe(true)
    await act(async () => {
      finish({ data: { notes: [{ id: "n" }] } })
    })
    expect(vi.mocked(loader).mock.calls.length).toBe(initialLoads)
  })

  it("stops tracking at the upload's timeout even across slow polls", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    markProcessing("appointment_new")
    client.noteDetail.mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(() => resolve({ data: { notes: [] } }), 500),
        ),
    )
    renderProcessing(
      vi.fn(
        () => new Promise((resolve) => setTimeout(() => resolve(null), 500)),
      ),
    )
    await screen.findByText("Writing your note…")
    const { result } = renderHook(() => useRecentUploads())

    for (let elapsed = 0; elapsed <= NOTE_PROCESSING_TIMEOUT_MS; ) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(NOTE_PROCESSING_POLL_MS)
      })
      elapsed += NOTE_PROCESSING_POLL_MS
    }

    expect(result.current.startedAt.has("appointment_new")).toBe(false)
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
    markProcessing("appointment_new")
    const { result } = renderHook(() => useRecentUploads())
    expect(isProcessing(result.current, "appointment_new")).toBe(true)
    expect(withUploadProgress(entry, result.current)).toMatchObject({
      status: "in-progress",
      statusLabel: "Writing note",
    })
  })

  it("highlights a visit whose note just arrived", () => {
    const result = withUploadProgress(
      { ...entry, status: "completed", statusLabel: "Note ready" },
      { startedAt: new Map(), justReady: new Set(["appointment_new"]) },
    )
    expect(result.highlight).toBe(true)
    expect(result.status).toBe("completed")
  })
})
