import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"

import { emptyHistory } from "./SovereignHistory.fixture"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"

const authority = vi.hoisted(() => ({
  getClient: vi.fn(),
  getViewAsStatus: vi.fn(),
}))
vi.mock("~/configuration/clerk", () => ({ getClient: authority.getClient }))
vi.mock("~/lib/view-as-api", () => ({
  getViewAsStatus: authority.getViewAsStatus,
}))

type PanelApi = NonNullable<Window["CosmaNeuraPanel"]>
type PanelOptions = Parameters<PanelApi["mount"]>[0]
type ScriptAttempt = {
  node: HTMLScriptElement
  onLoad: NonNullable<HTMLScriptElement["onload"]>
  onError: NonNullable<HTMLScriptElement["onerror"]>
  remove: ReturnType<typeof vi.spyOn>
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "X-Cosmaneura-Embed-Version": "1.0",
    },
  })
}

async function fixture() {
  vi.resetModules()
  vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true")
  vi.stubEnv(
    "VITE_SOVEREIGN_PROVIDER_URL",
    "https://provider-sovereign.example",
  )
  authority.getViewAsStatus.mockResolvedValue({ active: false })
  authority.getClient.mockResolvedValue({
    user: { id: "user_synthetic_load_recovery" },
    session: { getToken: vi.fn(async () => "synthetic-platform-token") },
  })
  const records: string[] = []
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/embed/v1/context-tokens/exchange") {
        records.push(JSON.parse(String(init?.body)).record_ref)
        const now = Date.now()
        return json({
          context_token: `mctx_${String(records.length).repeat(48)}`,
          token_type: "Bearer",
          issued_at: new Date(now - 1000).toISOString(),
          refresh_after: new Date(now + 300000).toISOString(),
          expires_at: new Date(now + 900000).toISOString(),
        })
      }
      if (String(input).endsWith("/embed/history-retained"))
        return json({ ...emptyHistory(), schema_version: "1.2.0" })
      if (String(input).endsWith("/welcome")) {
        return json({
          persona: "provider",
          greeting: "Synthetic assistant ready",
          paths: [],
        })
      }
      throw new Error("Unexpected synthetic request")
    },
  )
  vi.stubGlobal("fetch", fetchMock)
  const { SOVEREIGN_PANEL_ASSET_PATH, SOVEREIGN_PANEL_INTEGRITY } =
    await import("./SovereignPanelAsset")
  const source = readFileSync(
    join(process.cwd(), "public", SOVEREIGN_PANEL_ASSET_PATH),
    "utf8",
  )
  const scripts: ScriptAttempt[] = []
  const append = document.head.appendChild.bind(document.head)
  vi.spyOn(document.head, "appendChild").mockImplementation(
    <T extends Node>(node: T): T => {
      if (!(node instanceof HTMLScriptElement)) return append(node)
      // Simulate a browser request that never dispatches load or error.
      // No network request or automatic script evaluation occurs in this test.
      expect(node.src).toContain(SOVEREIGN_PANEL_ASSET_PATH)
      expect(node.integrity).toBe(SOVEREIGN_PANEL_INTEGRITY)
      expect(node.crossOrigin).toBe("anonymous")
      expect(node.referrerPolicy).toBe("no-referrer")
      expect(node.onload).not.toBeNull()
      expect(node.onerror).not.toBeNull()
      scripts.push({
        node,
        onLoad: node.onload!,
        onError: node.onerror!,
        remove: vi.spyOn(node, "remove"),
      })
      return node
    },
  )
  const mounts: PanelOptions[] = []
  const complete = async (attempt: ScriptAttempt) => {
    await act(async () => {
      // Run the exact pinned panel only when the simulated load completes.
      window.eval(source)
      const mount = window.CosmaNeuraPanel!.mount
      window.CosmaNeuraPanel!.mount = (options) => {
        mounts.push(options)
        return mount(options)
      }
      attempt.onLoad.call(attempt.node, new Event("load"))
    })
  }
  const staleCallbacks = async (attempt: ScriptAttempt) => {
    await act(async () => {
      attempt.onLoad.call(attempt.node, new Event("load"))
      attempt.onError.call(attempt.node, new Event("error"))
    })
  }
  const { SovereignPanelHost: Host } = await import("./SovereignPanelHost")
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
  return { Host, scripts, records, mounts, fetchMock, complete, staleCallbacks }
}

async function flush() {
  await act(async () => vi.advanceTimersByTimeAsync(0))
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  vi.clearAllMocks()
  delete window.CosmaNeuraPanel
})

describe("pending pinned panel load ownership", () => {
  it("releases a stalled load at the deadline and reconnects through a fresh pinned request", async () => {
    const f = await fixture()
    render(<f.Host appointmentId="appointment_synthetic_a" />)
    await flush()
    expect(f.scripts).toHaveLength(1)
    const old = f.scripts[0]
    await act(async () => vi.advanceTimersByTimeAsync(30_000))
    expect(old.remove).toHaveBeenCalledTimes(1)
    expect(old.node.onload).toBeNull()
    expect(old.node.onerror).toBeNull()
    expect(f.fetchMock).not.toHaveBeenCalled()
    expect(f.mounts).toHaveLength(0)
    fireEvent.click(screen.getByRole("button", { name: "Reconnect assistant" }))
    await flush()
    expect(f.scripts).toHaveLength(2)
    await f.staleCallbacks(old)
    expect(f.records).toEqual([])
    expect(f.scripts[1].remove).not.toHaveBeenCalled()
    await f.complete(f.scripts[1])
    await flush()
    expect(f.records).toEqual(["appointment_synthetic_a"])
    expect(f.mounts).toHaveLength(1)
    expect(authority.getViewAsStatus).toHaveBeenCalledTimes(2)
    expect(
      screen.queryByRole("button", { name: "Reconnect assistant" }),
    ).toBeNull()
    expect(
      document.querySelector<HTMLButtonElement>(".cnp-send")?.disabled,
    ).toBe(false)
    await f.staleCallbacks(old)
    expect(f.mounts).toHaveLength(1)
    expect(f.scripts[1].remove).not.toHaveBeenCalled()
    expect(f.fetchMock).toHaveBeenCalledTimes(3) // Fresh exchange, welcome and recent history.
  })

  it("releases a departing appointment's load and ignores its late callbacks", async () => {
    const f = await fixture()
    const view = render(<f.Host appointmentId="appointment_synthetic_a" />)
    await flush()
    const old = f.scripts[0]
    view.rerender(<f.Host appointmentId="appointment_synthetic_b" />)
    expect(old.remove).toHaveBeenCalledTimes(1)
    await flush()
    expect(f.scripts).toHaveLength(2)
    await f.staleCallbacks(old)
    expect(f.records).toEqual([])
    await f.complete(f.scripts[1])
    await flush()
    await f.staleCallbacks(old)
    expect(f.records).toEqual(["appointment_synthetic_b"])
    expect(f.mounts).toHaveLength(1)
    await expect(f.mounts[0].getContext()).resolves.toEqual({
      clientId: "appointment_synthetic_b",
    })
    expect(document.querySelectorAll(".cnp-root")).toHaveLength(1)
  })

  it("keeps a shared pending load when one consumer unmounts", async () => {
    const f = await fixture()
    const first = render(<f.Host appointmentId="appointment_synthetic_a" />)
    render(<f.Host appointmentId="appointment_synthetic_b" />)
    await flush()
    expect(f.scripts).toHaveLength(1)
    first.unmount()
    expect(f.scripts[0].remove).not.toHaveBeenCalled()
    await f.complete(f.scripts[0])
    await flush()
    expect(f.records).toEqual(["appointment_synthetic_b"])
    expect(f.mounts).toHaveLength(1)
  })

  it("keeps a later consumer alive across an earlier consumer's timeout", async () => {
    const f = await fixture()
    render(<f.Host appointmentId="appointment_synthetic_a" />)
    await flush()
    await act(async () => vi.advanceTimersByTimeAsync(20_000))
    render(<f.Host appointmentId="appointment_synthetic_b" />)
    await flush()
    await act(async () => vi.advanceTimersByTimeAsync(10_000))
    expect(
      screen.getByRole("button", { name: "Reconnect assistant" }),
    ).toBeEnabled()
    expect(f.scripts).toHaveLength(1)
    expect(f.scripts[0].remove).not.toHaveBeenCalled()
    await f.complete(f.scripts[0])
    await flush()
    expect(f.records).toEqual(["appointment_synthetic_b"])
    fireEvent.click(screen.getByRole("button", { name: "Reconnect assistant" }))
    await flush()
    expect(f.scripts).toHaveLength(1) // Reuse only the now-verified API.
    expect(f.records).toEqual([
      "appointment_synthetic_b",
      "appointment_synthetic_a",
    ])
    expect(f.mounts).toHaveLength(2)
  })

  it("does not trust a global registered after its canceled script callback", async () => {
    const f = await fixture()
    render(<f.Host appointmentId="appointment_synthetic_a" />)
    await flush()
    const old = f.scripts[0]
    await act(async () => vi.advanceTimersByTimeAsync(30_000))
    const unverifiedMount = vi.fn()
    window.CosmaNeuraPanel = { mount: unverifiedMount }
    await f.staleCallbacks(old)
    fireEvent.click(screen.getByRole("button", { name: "Reconnect assistant" }))
    await flush()
    expect(
      screen.getByRole("button", { name: "Reconnect assistant" }),
    ).toBeEnabled()
    expect(unverifiedMount).not.toHaveBeenCalled()
    expect(f.scripts).toHaveLength(1)
    expect(f.records).toEqual([])
  })
})
