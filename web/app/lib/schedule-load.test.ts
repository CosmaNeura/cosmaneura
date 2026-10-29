import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { BillingRecoveryRedirectError } from "~/lib/billing-recovery"
import { PRACTICE_BAA_REQUIRED_DETAIL } from "~/lib/practice-gate-error"
import { READ_DEADLINE_MS } from "~/lib/read-deadline"

import { loadSchedule } from "./schedule-load"

const { read } = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock("~/configuration/client", () => ({ scheduleList: read }))

function load(signal?: AbortSignal) {
  return loadSchedule(
    new Request("https://synthetic.invalid/schedule", { signal }),
  )
}

beforeEach(() => {
  read.mockReset()
})
afterEach(() => {
  vi.useRealTimers()
})

describe("bounded schedule reads", () => {
  it.each([403, 404, 503])(
    "does not mistake a failed HTTP%s list for an absent meeting",
    async (status) => {
      read.mockResolvedValue({
        response: new Response(null, { status }),
        error: { detail: "Private diagnostic" },
      })
      await expect(load()).rejects.toMatchObject({
        data: { scheduleLoadFailure: "temporary" },
        init: { status: 503 },
      })
    },
  )

  it("retains successful empty lists so only the detail route decides absence", async () => {
    const data = {
      meetings: [],
      zoom_connected: false,
      capture_available: false,
    }
    read.mockResolvedValue({
      data,
      response: new Response(null, { status: 200 }),
    })
    expect(await load()).toBe(data)
  })

  it("keeps an expired session distinct from a missing meeting", async () => {
    read.mockResolvedValue({ response: new Response(null, { status: 401 }) })
    await expect(load()).rejects.toMatchObject({
      data: { scheduleLoadFailure: "session" },
      init: { status: 401 },
    })
  })

  it("retains the exact practice gate instead of offering protected content", async () => {
    read.mockResolvedValue({
      response: new Response(null, { status: 403 }),
      error: { detail: PRACTICE_BAA_REQUIRED_DETAIL },
    })
    await expect(load()).rejects.toMatchObject({
      data: { kind: "accept_agreement" },
      init: { status: 403 },
    })
  })

  it.each(["returned", "thrown"])(
    "preserves a %s billing redirect",
    async (shape) => {
      const redirect = new BillingRecoveryRedirectError(
        "/settings?billing=required",
      )
      if (shape === "returned") read.mockResolvedValue({ error: redirect })
      else read.mockRejectedValue(redirect)
      await expect(load()).rejects.toBe(redirect)
    },
  )

  it("bounds a stalled SDK/auth read and cancels its request", async () => {
    vi.useFakeTimers()
    read.mockImplementation(() => new Promise(() => {}))
    const result = load().catch((error) => error)
    await vi.advanceTimersByTimeAsync(READ_DEADLINE_MS)
    expect(await result).toMatchObject({
      data: { scheduleLoadFailure: "temporary" },
      init: { status: 503 },
    })
    expect(read.mock.calls[0][0].signal.aborted).toBe(true)
  })

  it("does not replace navigation cancellation with a recovery screen", async () => {
    const controller = new AbortController()
    const reason = new DOMException("Navigation cancelled", "AbortError")
    controller.abort(reason)
    await expect(load(controller.signal)).rejects.toBe(reason)
    expect(read).not.toHaveBeenCalled()
  })
})
