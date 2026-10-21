import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"

import { emptyHistory } from "./SovereignHistory.fixture"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"

const authority = vi.hoisted(() => ({
  getClient: vi.fn(),
  getViewAsStatus: vi.fn(),
}))
vi.mock("~/configuration/clerk", () => ({ getClient: authority.getClient }))
vi.mock("~/lib/view-as-api", () => ({
  getViewAsStatus: authority.getViewAsStatus,
}))

const inactiveViewAs = {
  request_binding: "inactive" as const,
  can_view_as: false,
  active: false,
  read_only: false,
  diagnostic_code: null,
  expires_at: null,
  actor: null,
  target: null,
}

type PanelApi = NonNullable<Window["CosmaNeuraPanel"]>
type PanelOptions = Parameters<PanelApi["mount"]>[0]
type MountedPanel = ReturnType<PanelApi["mount"]> & { root: HTMLElement }

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "X-Cosmaneura-Embed-Version": "1.0",
    },
  })
}

function tokenResponse(token: string, now: number) {
  return json(
    {
      context_token: token,
      token_type: "Bearer",
      issued_at: new Date(now - 1000).toISOString(),
      refresh_after: new Date(now + 300000).toISOString(),
      expires_at: new Date(now + 900000).toISOString(),
    },
    201,
  )
}

function readyBrief(turn: string, recap: string) {
  return json({
    client_turn_id: turn,
    schema_version: "1.1.0",
    facts_state: "unavailable",
    structured_facts: null,
    state: "ready",
    freshness: "current",
    current_through: "2026-09-24T12:00:00Z",
    source_count: 1,
    prepared_brief: {
      recap,
      trajectory_and_material_changes: [],
      talking_points: ["Synthetic follow-up"],
      open_threads: [],
      cautions: [],
      suggested_outline: ["Review fictional interval changes"],
      patient_reported: null,
    },
    retry_after_seconds: null,
    reason_code: null,
  })
}

async function fixture() {
  // Each case loads the same real, byte-pinned panel in a fresh module scope.
  vi.resetModules()
  vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true")
  vi.stubEnv(
    "VITE_SOVEREIGN_PROVIDER_URL",
    "https://provider-sovereign.example",
  )
  vi.spyOn(document, "hidden", "get").mockReturnValue(false)
  const control = {
    now: Date.UTC(2026, 8, 24, 12),
    denyExchange: false,
    heldExchange: null as Promise<Response> | null,
    heldWelcome: null as Promise<Response> | null,
  }
  vi.spyOn(Date, "now").mockImplementation(() => control.now)
  authority.getViewAsStatus.mockResolvedValue(inactiveViewAs)
  const getClerkToken = vi.fn(async () => "synthetic-platform-session-token")
  authority.getClient.mockResolvedValue({
    user: { id: "user_synthetic_reconnect" },
    session: { getToken: getClerkToken },
  })
  const exchanges: Array<{
    record: string
    signal: AbortSignal
    authorization: string | null
  }> = []
  const prepRequests: Array<{ turn: string; authorization: string | null }> = []
  const welcomes: Array<string | null> = []
  const welcomeSignals: AbortSignal[] = []
  const tokens = ["a", "b", "c"].map((letter) => `mctx_${letter.repeat(48)}`)
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/embed/history")) return json(emptyHistory())
      const authorization = new Headers(init?.headers).get("authorization")
      if (url === "/embed/v1/context-tokens/exchange") {
        exchanges.push({
          record: JSON.parse(String(init?.body)).record_ref,
          signal: init!.signal!,
          authorization,
        })
        if (control.denyExchange) return json({ code: "REQUEST_DENIED" }, 403)
        if (exchanges.length === 2 && control.heldExchange)
          return control.heldExchange
        return tokenResponse(tokens[exchanges.length - 1], control.now)
      }
      if (url.endsWith("/welcome")) {
        welcomes.push(authorization)
        welcomeSignals.push(init!.signal!)
        if (welcomes.length === 2 && control.heldWelcome)
          return control.heldWelcome
        return json({
          partner_name: "Synthetic provider",
          persona: "provider",
          greeting: "Welcome",
          paths: [
            {
              label: "Open Session Prep",
              message: "Open Session Prep",
              intent: "session_prep",
            },
          ],
          disclaimer: "Synthetic test only.",
          emergency_note: "Call emergency services when needed.",
        })
      }
      if (url.endsWith("/session-prep/structured-facts")) {
        const turn = JSON.parse(String(init?.body)).client_turn_id
        prepRequests.push({ turn, authorization })
        return readyBrief(
          turn,
          prepRequests.length === 1
            ? "Synthetic old session brief"
            : "Synthetic fresh session brief",
        )
      }
      throw new Error(`unexpected synthetic request ${url}`)
    },
  )
  vi.stubGlobal("fetch", fetchMock)
  const { SOVEREIGN_PANEL_ASSET_PATH } = await import("./SovereignPanelAsset")
  const source = readFileSync(
    join(process.cwd(), "public", SOVEREIGN_PANEL_ASSET_PATH),
    "utf8",
  )
  const mounts: Array<{ options: PanelOptions; panel: MountedPanel }> = []
  vi.spyOn(document.head, "appendChild").mockImplementation(
    <T extends Node>(node: T): T => {
      if (node instanceof HTMLScriptElement) {
        window.eval(source)
        const mount = window.CosmaNeuraPanel!.mount
        window.CosmaNeuraPanel!.mount = (options) => {
          const panel = mount(options) as MountedPanel
          mounts.push({ options, panel })
          return panel
        }
        queueMicrotask(() => node.onload?.call(node, new Event("load")))
      }
      return node
    },
  )
  const { SovereignPanelHost: Host } = await import("./SovereignPanelHost")
  const view = render(<Host appointmentId="appointment_synthetic_a" />)
  await waitFor(() =>
    expect(
      document.querySelector<HTMLButtonElement>(".cnp-send")?.disabled,
    ).toBe(false),
  )
  const expireAndLock = async () => {
    document.querySelector<HTMLButtonElement>(".cnp-fab")!.click()
    control.now += 16 * 60_000
    await act(async () =>
      document.querySelector<HTMLButtonElement>(".cnp-prep-go")!.click(),
    )
    await screen.findByRole("button", { name: "Reconnect assistant" })
  }
  return {
    control,
    exchanges,
    prepRequests,
    welcomes,
    welcomeSignals,
    tokens,
    mounts,
    fetchMock,
    getClerkToken,
    Host,
    view,
    expireAndLock,
  }
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

describe("explicit assistant session reconnection", () => {
  it.each(["exchange", "welcome"])(
    "times out a stalled %s and fences its late result from the same-record retry",
    async (phase) => {
      const f = await fixture()
      await f.expireAndLock()
      let resolveStalled!: (response: Response) => void
      const stalled = new Promise<Response>((resolve) => {
        resolveStalled = resolve
      })
      if (phase === "exchange") f.control.heldExchange = stalled
      else f.control.heldWelcome = stalled
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "Reconnect assistant" }),
        )
        await vi.advanceTimersByTimeAsync(0)
      })
      const old = f.mounts[1]
      expect(f.exchanges).toHaveLength(2)
      expect(
        screen.getByRole("button", { name: "Reconnecting assistant…" }),
      ).toBeDisabled()
      await act(async () => vi.advanceTimersByTimeAsync(30_000))
      expect(
        screen.getByRole("button", { name: "Reconnect assistant" }),
      ).toBeEnabled()
      expect(old.panel.root.isConnected).toBe(false)
      expect(
        (phase === "exchange" ? f.exchanges[1].signal : f.welcomeSignals[1])
          .aborted,
      ).toBe(true)
      await expect(old.options.getAuthToken()).rejects.toThrow()
      vi.useRealTimers()

      fireEvent.click(
        screen.getByRole("button", { name: "Reconnect assistant" }),
      )
      await waitFor(() => expect(f.exchanges).toHaveLength(3))
      await waitFor(() =>
        expect(
          document.querySelector<HTMLButtonElement>(".cnp-send")?.disabled,
        ).toBe(false),
      )
      await act(async () => {
        resolveStalled(
          phase === "exchange"
            ? tokenResponse(f.tokens[1], f.control.now)
            : json({
                persona: "provider",
                greeting: "Late obsolete welcome",
                paths: [],
              }),
        )
        old.options.onAccessChange(true)
        old.options.onAccessChange(false)
      })
      expect(
        screen.queryByRole("button", { name: "Reconnect assistant" }),
      ).toBeNull()
      expect(document.querySelectorAll(".cnp-root")).toHaveLength(1)
      expect(document.body.textContent).not.toContain("Late obsolete welcome")
      expect(
        f.exchanges.every(({ record }) => record === "appointment_synthetic_a"),
      ).toBe(true)
      expect(f.welcomes.at(-1)).toBe(`Bearer ${f.tokens[2]}`)
      expect(f.prepRequests).toHaveLength(0)
      await expect(old.options.getContext()).rejects.toThrow()
      await expect(f.mounts[2].options.getContext()).resolves.toEqual({
        clientId: "appointment_synthetic_a",
      })
    },
  )

  it("reconnects after idle expiry with fresh authority and no replay of old content or turns", async () => {
    const f = await fixture()
    document.querySelector<HTMLButtonElement>(".cnp-fab")!.click()
    await act(async () =>
      document.querySelector<HTMLButtonElement>(".cnp-prep-go")!.click(),
    )
    await waitFor(() =>
      expect(document.querySelector(".cnp-prep-view")?.textContent).toContain(
        "Synthetic old session brief",
      ),
    )
    document.querySelector<HTMLTextAreaElement>(".cnp-in")!.value =
      "Synthetic old draft"
    f.control.now += 16 * 60_000
    await act(async () =>
      document.querySelector<HTMLButtonElement>(".cnp-prep-go")!.click(),
    )
    const reconnect = await screen.findByRole("button", {
      name: "Reconnect assistant",
    })
    const old = f.mounts[0]
    expect(old.panel.root.querySelector<HTMLElement>(".cnp-rail")?.hidden).toBe(
      true,
    )
    expect(document.body.textContent).not.toContain(
      "Synthetic old session brief",
    )
    expect(document.querySelector<HTMLTextAreaElement>(".cnp-in")!.value).toBe(
      "",
    )
    expect(f.prepRequests).toHaveLength(1)
    expect(f.exchanges).toHaveLength(1)
    await expect(old.options.getAuthToken()).rejects.toThrow(
      "Provider authority is unavailable",
    )

    fireEvent.click(reconnect)
    await waitFor(() => expect(f.mounts).toHaveLength(2))
    await waitFor(() =>
      expect(
        document.querySelector<HTMLButtonElement>(".cnp-send")?.disabled,
      ).toBe(false),
    )
    expect(old.panel.root.isConnected).toBe(false)
    expect(authority.getViewAsStatus).toHaveBeenCalledTimes(2)
    expect(f.getClerkToken).toHaveBeenCalledTimes(2)
    expect(f.exchanges.map(({ record }) => record)).toEqual([
      "appointment_synthetic_a",
      "appointment_synthetic_a",
    ])
    expect(
      f.exchanges.every(
        ({ authorization }) =>
          authorization === "Bearer synthetic-platform-session-token",
      ),
    ).toBe(true)
    expect(f.welcomes).toEqual(
      f.tokens.slice(0, 2).map((token) => `Bearer ${token}`),
    )
    expect(f.prepRequests).toHaveLength(1)
    expect(document.body.textContent).not.toContain(
      "Synthetic old session brief",
    )
    expect(document.querySelector<HTMLTextAreaElement>(".cnp-in")!.value).toBe(
      "",
    )
    await act(async () => {
      old.options.onAccessChange(true)
      old.options.onAccessChange(false)
    })
    expect(
      screen.queryByRole("button", { name: "Reconnect assistant" }),
    ).toBeNull()
    await expect(old.options.getContext()).rejects.toThrow()

    document.querySelector<HTMLButtonElement>(".cnp-fab")!.click()
    await act(async () =>
      document.querySelector<HTMLButtonElement>(".cnp-prep-go")!.click(),
    )
    await waitFor(() => expect(f.prepRequests).toHaveLength(2))
    expect(f.prepRequests[1].turn).not.toBe(f.prepRequests[0].turn)
    expect(f.prepRequests[1].authorization).toBe(`Bearer ${f.tokens[1]}`)
    expect(
      f.fetchMock.mock.calls.some(([url]) => String(url).endsWith("/rotate")),
    ).toBe(false)
  })

  it.each(["view-as", "signed-out", "exchange-denied"])(
    "keeps %s denial closed without replay or automatic reconnection",
    async (denial) => {
      const f = await fixture()
      await f.expireAndLock()
      if (denial === "view-as")
        authority.getViewAsStatus.mockResolvedValue({
          ...inactiveViewAs,
          active: true,
        })
      if (denial === "signed-out")
        authority.getClient.mockResolvedValue({ user: null, session: null })
      if (denial === "exchange-denied") f.control.denyExchange = true
      fireEvent.click(
        screen.getByRole("button", { name: "Reconnect assistant" }),
      )
      await screen.findByRole("button", { name: "Reconnect assistant" })
      expect(authority.getViewAsStatus).toHaveBeenCalledTimes(2)
      expect(f.exchanges).toHaveLength(denial === "exchange-denied" ? 2 : 1)
      expect(f.welcomes).toHaveLength(1)
      expect(f.prepRequests).toHaveLength(0)
      expect(document.querySelector(".cnp-root")).toBeNull()
      await act(async () => f.mounts[0].options.onAccessChange(true))
      expect(
        screen.getByRole("button", { name: "Reconnect assistant" }),
      ).toBeEnabled()
      expect(document.querySelector(".cnp-root")).toBeNull()
    },
  )

  it("discards late reconnect credentials and callbacks after the current record changes", async () => {
    const f = await fixture()
    await f.expireAndLock()
    let resolveExchange!: (value: Response) => void
    f.control.heldExchange = new Promise((resolve) => {
      resolveExchange = resolve
    })
    fireEvent.click(screen.getByRole("button", { name: "Reconnect assistant" }))
    await waitFor(() => expect(f.exchanges).toHaveLength(2))
    const stale = f.mounts[1]
    expect(
      screen.getByRole("button", { name: "Reconnecting assistant…" }),
    ).toBeDisabled()
    f.view.rerender(<f.Host appointmentId="appointment_synthetic_b" />)
    expect(f.exchanges[1].signal.aborted).toBe(true)
    expect(stale.panel.root.isConnected).toBe(false)
    await waitFor(() => expect(f.exchanges).toHaveLength(3))
    await waitFor(() =>
      expect(
        document.querySelector<HTMLButtonElement>(".cnp-send")?.disabled,
      ).toBe(false),
    )
    await act(async () => {
      stale.options.onAccessChange(true)
      stale.options.onAccessChange(false)
      resolveExchange(tokenResponse(f.tokens[1], f.control.now))
    })
    expect(
      screen.queryByRole("button", { name: "Reconnect assistant" }),
    ).toBeNull()
    expect(document.querySelectorAll(".cnp-root")).toHaveLength(1)
    expect(f.exchanges.map(({ record }) => record)).toEqual([
      "appointment_synthetic_a",
      "appointment_synthetic_a",
      "appointment_synthetic_b",
    ])
    expect(f.welcomes).toEqual([
      `Bearer ${f.tokens[0]}`,
      `Bearer ${f.tokens[2]}`,
    ])
    expect(f.prepRequests).toHaveLength(0)
    await expect(stale.options.getAuthToken()).rejects.toThrow(
      "Provider authority is unavailable",
    )
    await expect(stale.options.getContext()).rejects.toThrow()
    await expect(f.mounts[2].options.getContext()).resolves.toEqual({
      clientId: "appointment_synthetic_b",
    })
  })
})
