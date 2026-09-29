import { afterEach, describe, expect, it, vi } from "vitest"

import {
  HistoryAccessDeniedError,
  loadSovereignHistory,
} from "./SovereignHistory"
import { completedHistory, emptyHistory } from "./SovereignHistory.fixture"

const headers = {
  "Content-Type": "application/json",
  "X-Cosmaneura-Embed-Version": "1.0",
}
function response(value: unknown) {
  return new Response(JSON.stringify(value), { headers })
}
function read(signal = new AbortController().signal, assertCurrent = () => {}) {
  return loadSovereignHistory({
    origin: "https://provider-sovereign.example",
    getToken: async () => "mctx_synthetic_current_authority",
    signal,
    assertCurrent,
  })
}
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("governed recent-history transport", () => {
  it("sends only the closed recent selector with the current mount and no cookies", async () => {
    const value = completedHistory("🩺".repeat(20_000))
    const fetch = vi.fn(async () => response(value))
    vi.stubGlobal("fetch", fetch)
    await expect(read()).resolves.toEqual(value)
    expect(fetch).toHaveBeenCalledExactlyOnceWith(
      "https://provider-sovereign.example/embed/history",
      expect.objectContaining({
        method: "POST",
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        body: '{"schema_version":"1.0.0","mode":"recent","client_turn_id":null}',
        headers: expect.objectContaining({
          Authorization: "Bearer mctx_synthetic_current_authority",
        }),
      }),
    )
  })

  it.each([401, 403])(
    "preserves authorization denial %s instead of returning empty history",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response("not retained", { status })),
      )
      await expect(read()).rejects.toBeInstanceOf(HistoryAccessDeniedError)
    },
  )

  it.each([409, 503])(
    "does not turn temporary failure %s into empty history",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response("not retained", { status })),
      )
      await expect(read()).rejects.toThrow("Recent conversation is unavailable")
    },
  )

  it.each([
    [
      "extra selectors",
      (value: ReturnType<typeof completedHistory>) =>
        Object.assign(value, { record_ref: "foreign_record" }),
    ],
    [
      "unpaired final",
      (value: ReturnType<typeof completedHistory>) =>
        value.history.messages.shift(),
    ],
    [
      "stale answer",
      (value: ReturnType<typeof completedHistory>) => {
        value.turns[0].state = "stale"
        value.turns[0].outcome = null
      },
    ],
    [
      "duplicate turns",
      (value: ReturnType<typeof completedHistory>) =>
        value.turns.push(value.turns[0]),
    ],
    [
      "wrong turn",
      (value: ReturnType<typeof completedHistory>) => {
        value.history.messages[1].client_turn_id =
          "20000000-0000-4000-8000-000000000002"
      },
    ],
    [
      "naive timestamp",
      (value: ReturnType<typeof completedHistory>) => {
        value.turns[0].recorded_at = "2026-09-28T12:00:00"
      },
    ],
    [
      "oversized code points",
      (value: ReturnType<typeof completedHistory>) => {
        value.history.messages[1].content = "🩺".repeat(20_001)
      },
    ],
  ] as const)("rejects %s without echoing content", async (_name, mutate) => {
    const value = completedHistory()
    mutate(value)
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(value)),
    )
    await expect(read()).rejects.toThrow(/^Recent conversation is unavailable$/)
  })

  it("rejects total message bytes even when every message fits", async () => {
    const value = completedHistory("🩺".repeat(20_000))
    for (let i = 2; i <= 3; i++) {
      const next = completedHistory("🩺".repeat(20_000))
      for (const item of [...next.turns, ...next.history.messages]) {
        item.turn_ref += i
        item.client_turn_id = `10000000-0000-4000-8000-00000000000${i}`
      }
      value.turns.push(...next.turns)
      value.history.messages.push(...next.history.messages)
    }
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(value)),
    )
    await expect(read()).rejects.toThrow("Recent conversation is unavailable")
  })

  it.each(["declared", "streamed"])(
    "cancels a %s oversized body before parsing",
    async (mode) => {
      const cancel = vi.fn()
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          if (mode === "streamed")
            controller.enqueue(new Uint8Array(256 * 1024 + 1))
        },
        cancel,
      })
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response(stream, {
              headers: {
                ...headers,
                ...(mode === "declared" ? { "Content-Length": "262145" } : {}),
              },
            }),
        ),
      )
      await expect(read()).rejects.toThrow("Recent conversation is unavailable")
      expect(cancel).toHaveBeenCalledOnce()
    },
  )

  it("cancels a stalled body and rejects an obsolete generation", async () => {
    const controller = new AbortController()
    const cancel = vi.fn()
    let reading!: () => void
    const started = new Promise<void>((resolve) => {
      reading = resolve
    })
    const stream = new ReadableStream<Uint8Array>({
      pull() {
        reading()
      },
      cancel,
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(stream, { headers })),
    )
    const operation = read(controller.signal)
    const rejection = expect(operation).rejects.toMatchObject({
      name: "AbortError",
    })
    await started
    controller.abort()
    await rejection
    expect(cancel).toHaveBeenCalledOnce()
  })

  it("checks generation again after fresh authority resolves, before sending history", async () => {
    let resolve!: (token: string) => void
    const token = new Promise<string>((r) => {
      resolve = r
    })
    let current = true
    const fetch = vi.fn()
    vi.stubGlobal("fetch", fetch)
    const operation = loadSovereignHistory({
      origin: "https://provider-sovereign.example",
      getToken: () => token,
      signal: new AbortController().signal,
      assertCurrent: () => {
        if (!current) throw new DOMException("Cancelled", "AbortError")
      },
    })
    const rejection = expect(operation).rejects.toMatchObject({
      name: "AbortError",
    })
    current = false
    resolve("mctx_late")
    await rejection
    expect(fetch).not.toHaveBeenCalled()
  })

  it("allows content-free pending/stale statuses and successful empty history", async () => {
    const value = emptyHistory()
    value.turns = completedHistory().turns.map((item) => ({
      ...item,
      state: "pending",
      outcome: null,
    }))
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response(value))
      .mockResolvedValueOnce(response(emptyHistory()))
    vi.stubGlobal("fetch", fetch)
    await expect(read()).resolves.toEqual(value)
    await expect(read()).resolves.toEqual(emptyHistory())
  })
})
