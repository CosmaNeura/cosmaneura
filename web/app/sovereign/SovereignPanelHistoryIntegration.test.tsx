import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"

import { completedHistory } from "./SovereignHistory.fixture"
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
type Options = Parameters<NonNullable<Window["CosmaNeuraPanel"]>["mount"]>[0]
const headers = {
  "Content-Type": "application/json",
  "X-Cosmaneura-Embed-Version": "1.0",
}
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers })

async function fixture(
  first: () => Promise<Response> = async () => json(completedHistory()),
) {
  vi.resetModules()
  vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true")
  vi.stubEnv(
    "VITE_SOVEREIGN_PROVIDER_URL",
    "https://provider-sovereign.example",
  )
  vi.spyOn(document, "hidden", "get").mockReturnValue(false)
  authority.getViewAsStatus.mockResolvedValue({ active: false })
  const getToken = vi.fn(async () => "synthetic_clerk_session")
  authority.getClient.mockResolvedValue({
    user: { id: "user_synthetic_history" },
    session: { getToken },
  })
  const control = { history: first }
  const requests: Array<{ url: string; init: RequestInit }> = []
  const tokens: string[] = []
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    requests.push({ url, init: init! })
    if (url === "/embed/v1/context-tokens/exchange") {
      const token = `mctx_${String(tokens.length + 1).repeat(48)}`
      tokens.push(token)
      const now = Date.now()
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
    if (url.endsWith("/welcome"))
      return json({
        partner_name: "Synthetic provider",
        persona: "provider",
        greeting: "Synthetic welcome",
        paths: [],
        disclaimer: "Synthetic test only.",
      })
    if (url.endsWith("/embed/history")) return control.history()
    throw new Error("Unexpected synthetic request")
  })
  vi.stubGlobal("fetch", fetch)
  const { SOVEREIGN_PANEL_ASSET_PATH } = await import("./SovereignPanelAsset")
  const source = readFileSync(
    join(process.cwd(), "public", SOVEREIGN_PANEL_ASSET_PATH),
    "utf8",
  )
  const mounts: Options[] = []
  vi.spyOn(document.head, "appendChild").mockImplementation(
    <T extends Node>(node: T): T => {
      if (node instanceof HTMLScriptElement) {
        window.eval(source)
        const mount = window.CosmaNeuraPanel!.mount
        window.CosmaNeuraPanel!.mount = (options) => {
          mounts.push(options)
          return mount(options)
        }
        queueMicrotask(() => node.onload?.call(node, new Event("load")))
      }
      return node
    },
  )
  const { SovereignPanelHost: Host } = await import("./SovereignPanelHost")
  const view = render(<Host appointmentId="appointment_synthetic_a" />)
  const historyRequests = () =>
    requests.filter((item) => item.url.endsWith("/embed/history"))
  return {
    Host,
    view,
    control,
    requests,
    tokens,
    mounts,
    getToken,
    fetch,
    historyRequests,
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

describe("real panel governed-history bridge", () => {
  it("hydrates sixty clinical timeline sections grouped by visit with the latest open", async () => {
    const dates = [
      "2026-09-16",
      "2026-09-23",
      "2026-09-30",
      "2026-10-07",
      "2026-10-08",
    ]
    const headings = [
      "S - SUBJECTIVE",
      "Medications",
      "O - OBJECTIVE",
      "Observed and patient-assisted exam via video",
      "Exam deferred to in-person visit 08/26/2026",
      "Labs - fasting, 02/12/2026",
      "Risk calculations",
      "Screening gaps",
      "A - ASSESSMENT",
      "P - PLAN",
      "Follow-ups",
      "Return precautions - given verbally, repeated back accurately, sent in writing",
    ]
    const sections = Array.from(
      { length: 60 },
      (_, index) =>
        `[${index + 1}] ${dates[Math.floor(index / 12)]} — ${headings[index % 12]}\n\n` +
        `> Synthetic section ${index + 1}\n> Literal <img src=x onerror=alert(1)> text`,
    )
    const answer =
      "Reviewed visit timeline — exact source sections:\n\n" +
      sections.join("\n\n") +
      "\n\n5 selected reviewed-note versions; not the entire chart."
    const f = await fixture(async () => json(completedHistory(answer)))
    await screen.findByText("Synthetic recorded question")
    await waitFor(() =>
      expect(document.querySelectorAll(".cnp-evidence-card")).toHaveLength(60),
    )
    const cards = document.querySelectorAll(".cnp-evidence-card")
    expect(cards[59].textContent).toContain("Synthetic section 60")
    expect(cards[59].textContent).toContain(
      "Literal <img src=x onerror=alert(1)> text",
    )
    expect(document.querySelector(".cnp-evidence-card img")).toBeNull()
    expect(document.querySelector(".cnp-root")?.textContent).toContain(
      "5 selected reviewed-note versions; not the entire chart.",
    )
    const visits = Array.from(
      document.querySelectorAll<HTMLDetailsElement>(".cnp-evidence-visit"),
    )
    expect(visits).toHaveLength(5)
    expect(visits.map((visit) => visit.open)).toEqual(
      dates.map((date) => date === dates[4]),
    )
    expect(f.historyRequests()).toHaveLength(1)
    expect(f.requests.some((request) => request.url.endsWith("/chat"))).toBe(
      false,
    )
  })

  it("hydrates exact recorded messages and sends no browser selectors or chat", async () => {
    const f = await fixture()
    await screen.findByText("Synthetic recorded answer")
    expect(screen.getByText("Synthetic recorded question")).toBeInTheDocument()
    expect(
      document.querySelector<HTMLButtonElement>(".cnp-send")?.disabled,
    ).toBe(false)
    expect(f.historyRequests()).toHaveLength(1)
    const request = f.historyRequests()[0].init
    expect(request.body).toBe(
      '{"schema_version":"1.0.0","mode":"recent","client_turn_id":null}',
    )
    expect(new Headers(request.headers).get("Authorization")).toBe(
      `Bearer ${f.tokens[0]}`,
    )
    expect(request).toMatchObject({
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
    })
    expect(f.requests.some((item) => item.url.endsWith("/chat"))).toBe(false)
    expect(document.querySelector(".cnp-card")).toBeNull()
  })

  it.each([401, 403])(
    "closes on history %s and reconnects with fresh record authority",
    async (status) => {
      const f = await fixture(async () =>
        json({ code: "CONTEXT_INVALID" }, status),
      )
      fireEvent.click(
        await screen.findByRole("button", { name: "Reconnect assistant" }),
      )
      // The pending retry reads control.history only after this synchronous turn.
      f.control.history = async () =>
        json(completedHistory("Fresh authorized conversation"))
      await screen.findByText("Fresh authorized conversation")
      expect(f.tokens).toHaveLength(2)
      expect(f.getToken).toHaveBeenCalledTimes(2)
      expect(authority.getViewAsStatus).toHaveBeenCalledTimes(2)
      expect(
        new Headers(f.historyRequests()[1].init.headers).get("Authorization"),
      ).toBe(`Bearer ${f.tokens[1]}`)
      await expect(f.mounts[0].getAuthToken()).rejects.toThrow()
      expect(f.requests.some((item) => item.url.endsWith("/chat"))).toBe(false)
    },
  )

  it("aborts and discards a late previous-record response even if fetch ignores abort", async () => {
    let resolve!: (response: Response) => void
    const first = new Promise<Response>((r) => {
      resolve = r
    })
    const f = await fixture(() => first)
    await waitFor(() => expect(f.historyRequests()).toHaveLength(1))
    f.control.history = async () =>
      json(completedHistory("Current record conversation"))
    f.view.rerender(<f.Host appointmentId="appointment_synthetic_b" />)
    await screen.findByText("Current record conversation")
    expect(f.historyRequests()[0].init.signal?.aborted).toBe(true)
    await act(async () => {
      resolve(json(completedHistory("Obsolete record answer")))
    })
    expect(document.body.textContent).not.toContain("Obsolete record answer")
    expect(document.querySelectorAll(".cnp-root")).toHaveLength(1)
    expect(
      document.querySelector<HTMLButtonElement>(".cnp-send")?.disabled,
    ).toBe(false)
    const before = f.fetch.mock.calls.length
    await expect(
      f.mounts[1].loadHistory("appointment_synthetic_a", {
        signal: new AbortController().signal,
      }),
    ).rejects.toMatchObject({ name: "AbortError" })
    expect(f.fetch).toHaveBeenCalledTimes(before)
  })

  it.each(["invalid", "oversized", "changed", "unavailable"])(
    "keeps a %s history response closed",
    async (kind) => {
      const f = await fixture(async () => {
        if (kind === "changed" || kind === "unavailable")
          return json({}, kind === "changed" ? 409 : 503)
        if (kind === "oversized")
          return new Response("x".repeat(256 * 1024 + 1), { headers })
        return json({
          ...completedHistory("Unverified answer must not render"),
          record_ref: "unexpected_selector",
        })
      })
      await screen.findByRole("button", { name: "Reconnect assistant" })
      expect(document.body.textContent).not.toContain(
        "Unverified answer must not render",
      )
      expect(document.querySelector(".cnp-root")).toBeNull()
      expect(f.requests.some((item) => item.url.endsWith("/chat"))).toBe(false)
    },
  )

  it("preserves the thirty-second bootstrap boundary for stalled history and explicit retry", async () => {
    let resolve!: (response: Response) => void
    const first = new Promise<Response>((r) => {
      resolve = r
    })
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
    const f = await fixture(() => first)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(f.historyRequests()).toHaveLength(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(f.historyRequests()[0].init.signal?.aborted).toBe(true)
    f.control.history = async () =>
      json(completedHistory("Recovered conversation"))
    vi.useRealTimers()
    fireEvent.click(screen.getByRole("button", { name: "Reconnect assistant" }))
    await screen.findByText("Recovered conversation")
    await act(async () => {
      resolve(json(completedHistory("Timed out answer")))
    })
    expect(document.body.textContent).not.toContain("Timed out answer")
    expect(f.requests.some((item) => item.url.endsWith("/chat"))).toBe(false)
  })

  it("aborts active history on access loss and never renders its content", async () => {
    const f = await fixture()
    await screen.findByText("Synthetic recorded answer")
    let resolve!: (response: Response) => void
    f.control.history = () =>
      new Promise<Response>((r) => {
        resolve = r
      })
    const operation = f.mounts[0].loadHistory("appointment_synthetic_a", {
      signal: new AbortController().signal,
    })
    const rejection = expect(operation).rejects.toMatchObject({
      name: "AbortError",
    })
    await waitFor(() => expect(f.historyRequests()).toHaveLength(2))
    act(() => f.mounts[0].onAccessChange(false))
    expect(f.historyRequests()[1].init.signal?.aborted).toBe(true)
    resolve(json(completedHistory("Answer after denied access")))
    await rejection
    expect(document.body.textContent).not.toContain(
      "Answer after denied access",
    )
    await expect(f.mounts[0].getAuthToken()).rejects.toThrow()
  })
})
