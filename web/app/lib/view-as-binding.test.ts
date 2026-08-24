import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  VIEW_AS_REQUEST_BINDING_HEADER,
  ViewAsBindingStaleError,
  bindInternalApiRequest,
  commitViewAsControlTransition,
  enforceViewAsBindingResponse,
  ensureViewAsRequestBinding,
  isViewAsControlRequest,
  isViewAsRequestBinding,
  rawViewAsControlRequest,
  resetViewAsBindingStateForTests,
  subscribeToViewAsStateChanges,
} from "./view-as-binding"

const ACTIVE_BINDING = `vab1.${"a".repeat(64)}` as const
const getClient = vi.fn()

vi.mock("~/configuration/clerk", () => ({
  getClient: (...args: unknown[]) => getClient(...args),
}))

beforeEach(() => {
  window.sessionStorage.clear()
  window.localStorage.clear()
  resetViewAsBindingStateForTests()
  getClient.mockReset().mockResolvedValue({
    session: { getToken: vi.fn().mockResolvedValue("test-token") },
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
})

describe("view-as request binding", () => {
  it("accepts only inactive or a versioned 256-bit opaque binding", () => {
    expect(isViewAsRequestBinding("inactive")).toBe(true)
    expect(isViewAsRequestBinding(ACTIVE_BINDING)).toBe(true)
    expect(isViewAsRequestBinding(`vab1.${"A".repeat(64)}`)).toBe(false)
    expect(isViewAsRequestBinding(`vab1.${"a".repeat(63)}`)).toBe(false)
    expect(isViewAsRequestBinding("active")).toBe(false)
  })

  it("deduplicates bootstrap and sends an explicit inactive binding", async () => {
    const bootstrap = vi.fn().mockResolvedValue({ request_binding: "inactive" })

    await expect(
      Promise.all([
        ensureViewAsRequestBinding(bootstrap),
        ensureViewAsRequestBinding(bootstrap),
      ]),
    ).resolves.toEqual(["inactive", "inactive"])
    expect(bootstrap).toHaveBeenCalledTimes(1)

    const init = await bindInternalApiRequest(
      "https://api.example/internal/v1/settings",
      {
        headers: {
          Authorization: "Bearer test",
          [VIEW_AS_REQUEST_BINDING_HEADER]: "spoofed",
        },
      },
    )
    const headers = new Headers(init.headers)
    expect(headers.get("Authorization")).toBe("Bearer test")
    expect(headers.get(VIEW_AS_REQUEST_BINDING_HEADER)).toBe("inactive")
  })

  it("updates only the current tab expectation and publishes no target data", async () => {
    const posted: unknown[] = []
    class TestBroadcastChannel {
      postMessage(value: unknown) {
        posted.push(value)
      }

      close() {}
    }
    vi.stubGlobal("BroadcastChannel", TestBroadcastChannel)

    commitViewAsControlTransition({ request_binding: ACTIVE_BINDING })

    const init = await bindInternalApiRequest("/internal/v1/notes")
    expect(new Headers(init.headers).get(VIEW_AS_REQUEST_BINDING_HEADER)).toBe(
      ACTIVE_BINDING,
    )
    expect(posted).toEqual([{ type: "state-changed" }])
  })

  it("keeps a successful transition successful when BroadcastChannel throws", async () => {
    class ThrowingBroadcastChannel {
      postMessage() {
        throw new Error("channel unavailable")
      }

      close() {}
    }
    vi.stubGlobal("BroadcastChannel", ThrowingBroadcastChannel)

    expect(() =>
      commitViewAsControlTransition({ request_binding: ACTIVE_BINDING }),
    ).not.toThrow()

    const init = await bindInternalApiRequest("/internal/v1/notes")
    expect(new Headers(init.headers).get(VIEW_AS_REQUEST_BINDING_HEADER)).toBe(
      ACTIVE_BINDING,
    )
  })

  it("falls back to storage events when BroadcastChannel construction fails", () => {
    class ThrowingBroadcastChannel {
      constructor() {
        throw new Error("channel unavailable")
      }
    }
    vi.stubGlobal("BroadcastChannel", ThrowingBroadcastChannel)
    const callback = vi.fn()

    const unsubscribe = subscribeToViewAsStateChanges(callback)
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "cosmaneura:view-as-state-changed:v1",
        newValue: "state-changed",
      }),
    )
    unsubscribe()

    expect(callback).toHaveBeenCalledOnce()
  })

  it("uses raw fetch for control calls without a request-binding header", async () => {
    vi.stubEnv("VITE_PYTHON_URL", "https://api.example")
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ request_binding: "inactive" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    await expect(rawViewAsControlRequest("/status")).resolves.toEqual({
      request_binding: "inactive",
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("https://api.example/internal/v1/view-as/status")
    const headers = new Headers(init.headers)
    expect(headers.get("Authorization")).toBe("Bearer test-token")
    expect(headers.has(VIEW_AS_REQUEST_BINDING_HEADER)).toBe(false)
  })

  it("recognizes only the four binding-exempt control endpoints", () => {
    const current = "https://app.example/notes"
    expect(isViewAsControlRequest("/internal/v1/view-as/status", current)).toBe(
      true,
    )
    expect(
      isViewAsControlRequest(
        "/internal/v1/view-as/targets?ready=true",
        current,
      ),
    ).toBe(true)
    expect(
      isViewAsControlRequest("/internal/v1/view-as/sessions/", current),
    ).toBe(true)
    expect(
      isViewAsControlRequest("/internal/v1/view-as/sessions/current", current),
    ).toBe(true)
    expect(isViewAsControlRequest("/internal/v1/view-as/audit", current)).toBe(
      false,
    )
  })

  it("hard-blocks after a tagged stale response and reloads at most once", async () => {
    const reload = vi.fn()
    const staleResponse = () =>
      new Response(
        JSON.stringify({
          detail: {
            code: "VIEW_AS_BINDING_STALE",
            message: "The account view changed. Reload before continuing.",
          },
        }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      )

    await expect(
      enforceViewAsBindingResponse(staleResponse(), reload),
    ).rejects.toBeInstanceOf(ViewAsBindingStaleError)
    await expect(
      enforceViewAsBindingResponse(staleResponse(), reload),
    ).rejects.toBeInstanceOf(ViewAsBindingStaleError)
    expect(reload).toHaveBeenCalledTimes(1)
    await expect(
      ensureViewAsRequestBinding(
        vi.fn().mockResolvedValue({ request_binding: "inactive" }),
      ),
    ).rejects.toBeInstanceOf(ViewAsBindingStaleError)
  })

  it("leaves unrelated conflicts untouched", async () => {
    const response = new Response(
      JSON.stringify({ detail: { code: "ACCOUNT_RECOVERY_REQUIRED" } }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    )

    await expect(enforceViewAsBindingResponse(response)).resolves.toBe(response)
  })
})
