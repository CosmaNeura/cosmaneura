import { beforeEach, describe, expect, it, vi } from "vitest"

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

const args = () =>
  ({
    params: { id: "appt_synthetic" },
    request: new Request("https://example.invalid/notes/appt_synthetic"),
  }) as Parameters<typeof clientLoader>[0]

beforeEach(() => {
  noteDetailMock.mockReset()
})

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
    const failure = new Error("request cancelled")
    noteDetailMock.mockRejectedValue(failure)
    const loaderArgs = args()
    await expect(clientLoader(loaderArgs)).rejects.toBe(failure)
    expect(noteDetailMock).toHaveBeenCalledWith({
      path: { appointment_id: "appt_synthetic" },
      signal: loaderArgs.request.signal,
      throwOnError: false,
    })
  })

  it("does not turn an unrelated server failure into an agreement wall", async () => {
    noteDetailMock.mockResolvedValue({
      error: { detail: PRACTICE_BAA_REQUIRED_DETAIL },
      response: new Response(null, { status: 500 }),
      request: new Request("https://example.invalid"),
    })
    const failure = await clientLoader(args()).catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(Error)
    expect(practiceAgreementBlockFromRouteFailure(failure)).toBeNull()
  })
})
