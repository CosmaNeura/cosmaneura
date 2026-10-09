import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  UPLOADED_VISIT_LOOKUP_MS,
  findUploadedAppointmentId,
} from "./uploaded-visit"

const layout = vi.hoisted(() => ({ load: vi.fn() }))
vi.mock("~/layouts/notes", () => ({ loadNotesLayoutData: layout.load }))

const listing = (...ids: string[]) => ({
  appointments: ids.map((id) => ({ id })),
  noteStatuses: {},
  blocked: null,
})

beforeEach(() => {
  layout.load.mockReset()
})

describe("finding the uploaded visit", () => {
  it("returns the single visit that appeared, using the layout's gated read", async () => {
    layout.load.mockResolvedValue(listing("a", "b", "new"))
    await expect(findUploadedAppointmentId(new Set(["a", "b"]))).resolves.toBe(
      "new",
    )
    expect(layout.load).toHaveBeenCalledOnce()
  })

  it.each([
    ["nothing new", listing("a")],
    ["several new", listing("a", "x", "y")],
    ["a practice-agreement block", { ...listing("a", "new"), blocked: {} }],
  ])("does not guess when there is %s", async (_, after) => {
    layout.load.mockResolvedValue(after)
    await expect(findUploadedAppointmentId(new Set(["a"]))).resolves.toBeNull()
  })

  it("does not guess without a prior list or when the read fails", async () => {
    await expect(findUploadedAppointmentId(null)).resolves.toBeNull()
    expect(layout.load).not.toHaveBeenCalled()
    layout.load.mockRejectedValue(new Error("offline"))
    await expect(findUploadedAppointmentId(new Set(["a"]))).resolves.toBeNull()
  })

  it("gives up on a slow read instead of holding the upload open", async () => {
    vi.useFakeTimers()
    layout.load.mockReturnValue(new Promise(() => {}))
    const pending = findUploadedAppointmentId(new Set(["a"]))

    await vi.advanceTimersByTimeAsync(UPLOADED_VISIT_LOOKUP_MS)

    await expect(pending).resolves.toBeNull()
    vi.useRealTimers()
  })
})
