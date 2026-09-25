import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { NOTE_DETAIL_TIMEOUT_MS } from "~/lib/note-detail-load"
import {
  PRACTICE_BAA_REQUIRED_DETAIL,
  PRACTICE_MEMBERSHIP_REQUIRED_DETAIL,
  practiceAgreementBlockFromRouteFailure,
} from "~/lib/practice-gate-error"

import { clientLoader } from "./detail"

const { noteDetailMock } = vi.hoisted(() => ({ noteDetailMock: vi.fn() }))

vi.mock("~/configuration/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/configuration/client")>()),
  noteDetail: noteDetailMock,
}))

const args = (signal?: AbortSignal) =>
  ({
    params: { id: "appt_synthetic" },
    request: new Request("https://example.invalid/notes/appt_synthetic", {
      signal,
    }),
  }) as Parameters<typeof clientLoader>[0]

beforeEach(() => {
  noteDetailMock.mockReset()
})
afterEach(() => vi.useRealTimers())

describe("notes detail access recovery", () => {
  it.each([
    [PRACTICE_MEMBERSHIP_REQUIRED_DETAIL, "join_practice"],
    [PRACTICE_BAA_REQUIRED_DETAIL, "accept_agreement"],
  ])("preserves a returned 403 recovery action: %s", async (detail, kind) => {
    noteDetailMock.mockResolvedValue({
      error: { detail },
      response: new Response(null, { status: 403 }),
      request: new Request("https://example.invalid"),
    })
    const failure = await clientLoader(args()).catch((error: unknown) => error)
    expect(practiceAgreementBlockFromRouteFailure(failure)?.kind).toBe(kind)
  })

  it("passes cancellation to the detail request", async () => {
    const controller = new AbortController()
    const failure = new Error("request cancelled")
    noteDetailMock.mockReturnValue(new Promise(() => {}))
    const loaderArgs = args(controller.signal)
    const pending = clientLoader(loaderArgs)
    const cancelled = expect(pending).rejects.toBe(failure)
    controller.abort(failure)
    await cancelled
    expect(noteDetailMock).toHaveBeenCalledWith({
      path: { appointment_id: "appt_synthetic" },
      signal: expect.any(AbortSignal),
      throwOnError: false,
    })
    expect(noteDetailMock.mock.calls[0][0].signal.aborted).toBe(true)
  })

  it("does not turn an unrelated server failure into an agreement wall", async () => {
    noteDetailMock.mockResolvedValue({
      error: { detail: PRACTICE_BAA_REQUIRED_DETAIL },
      response: new Response(null, { status: 500 }),
      request: new Request("https://example.invalid"),
    })
    const failure = await clientLoader(args()).catch((error: unknown) => error)
    expect(failure).toMatchObject({
      data: { noteLoadFailure: "temporary" },
      init: { status: 503 },
    })
    expect(practiceAgreementBlockFromRouteFailure(failure)).toBeNull()
  })

  it.each([400, 403, 404, 422])(
    "gives a safe unavailable result for %s",
    async (status) => {
      noteDetailMock.mockResolvedValue({
        error: {
          detail: "Private error content must not reach the recovery page",
        },
        response: new Response(null, { status }),
      })
      await expect(clientLoader(args())).rejects.toMatchObject({
        data: { noteLoadFailure: "unavailable" },
        init: { status: 404 },
      })
    },
  )

  it("keeps a session failure distinct", async () => {
    noteDetailMock.mockResolvedValue({
      response: new Response(null, { status: 401 }),
    })
    await expect(clientLoader(args())).rejects.toMatchObject({
      data: { noteLoadFailure: "session" },
      init: { status: 401 },
    })
  })

  it("recovers from network failure without propagating exception content", async () => {
    noteDetailMock.mockRejectedValue(new TypeError("Private URL and response"))
    await expect(clientLoader(args())).rejects.toMatchObject({
      data: { noteLoadFailure: "temporary" },
      init: { status: 503 },
    })
  })

  it("rejects successful responses with no usable data", async () => {
    noteDetailMock.mockResolvedValue({
      response: new Response(null, { status: 200 }),
    })
    await expect(clientLoader(args())).rejects.toMatchObject({
      data: { noteLoadFailure: "temporary" },
      init: { status: 503 },
    })
  })

  it("does not start a read after navigation already cancelled", async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(clientLoader(args(controller.signal))).rejects.toMatchObject({
      name: "AbortError",
    })
    expect(noteDetailMock).not.toHaveBeenCalled()
  })

  it("bounds a stalled read and aborts it without accepting a late result", async () => {
    vi.useFakeTimers()
    let finish!: (value: unknown) => void
    noteDetailMock.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    const pending = clientLoader(args())
    const failed = expect(pending).rejects.toMatchObject({
      data: { noteLoadFailure: "temporary" },
      init: { status: 503 },
    })
    await vi.advanceTimersByTimeAsync(NOTE_DETAIL_TIMEOUT_MS)
    await failed
    expect(noteDetailMock.mock.calls[0][0].signal.aborted).toBe(true)
    finish({ data: { notes: [{ id: "late_note" }] } })
    await Promise.resolve()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("clears the deadline and preserves exact-note focus after success", async () => {
    vi.useFakeTimers()
    noteDetailMock.mockResolvedValue({
      data: { notes: [{ id: "reviewed_note" }] },
    })
    const loaderArgs = args()
    loaderArgs.request = new Request(
      "https://example.invalid/notes/appt_synthetic?reviewNote=reviewed_note",
    )
    await expect(clientLoader(loaderArgs)).resolves.toMatchObject({
      focusedNoteId: "reviewed_note",
    })
    expect(vi.getTimerCount()).toBe(0)
  })
})
