import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  citationFixture,
  excerptAnswer,
  finalReference,
} from "./SovereignCitationDisplay.fixture"
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
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers })

async function fixture(first = async () => json(citationFixture())) {
  vi.resetModules()
  vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true")
  vi.stubEnv("VITE_SOVEREIGN_PROVIDER_URL", "https://provider.example")
  vi.spyOn(document, "hidden", "get").mockReturnValue(false)
  authority.getViewAsStatus.mockResolvedValue({ active: false })
  authority.getClient.mockResolvedValue({
    user: { id: "synthetic-user" },
    session: { getToken: async () => "synthetic-session" },
  })
  const control = {
    retained: first,
    fresh: async () => json(citationFixture("1.1.0")),
  }
  const requests: Array<{ url: string; init: RequestInit }> = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit) => {
      const url = String(input)
      requests.push({ url, init })
      if (url === "/embed/v1/context-tokens/exchange") {
        const now = Date.now()
        return json(
          {
            context_token: "mctx_" + "a".repeat(48),
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
          persona: "provider",
          partner_name: "Synthetic",
          greeting: "Synthetic welcome",
          paths: [],
          disclaimer: "Synthetic provider disclaimer.",
        })
      if (url.endsWith("/embed/history-retained")) return control.retained()
      if (url.endsWith("/embed/history-with-citations")) return control.fresh()
      if (url.endsWith("/chat"))
        return json({
          client_turn_id: JSON.parse(String(init.body)).client_turn_id,
          answer: excerptAnswer,
          presentation: { mode: "standard", suppress: [], human_contact: null },
          citations: [],
          structured: {},
          doctrinal_review: null,
        })
      throw new Error("Unexpected synthetic request")
    }),
  )
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
        const original = window.CosmaNeuraPanel!.mount
        window.CosmaNeuraPanel!.mount = (options) => {
          mounts.push(options)
          return original(options)
        }
        queueMicrotask(() => node.onload?.call(node, new Event("load")))
      }
      return node
    },
  )
  const { SovereignPanelHost: Host } = await import("./SovereignPanelHost")
  const view = render(<Host appointmentId="appointment_synthetic_a" />)
  return { control, requests, mounts, view, Host }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  vi.clearAllMocks()
  delete window.CosmaNeuraPanel
})
describe("real byte-pinned panel with accepted Core maps", () => {
  it("restores a superseded answer then sends only the new question envelope, never retained history or maps", async () => {
    const f = await fixture()
    await waitFor(() =>
      expect(document.querySelectorAll(".cnp-reviewed-citation")).toHaveLength(
        2,
      ),
    )
    expect(document.body.textContent).toContain(
      "Historical answer · its reviewed sources have since changed",
    )
    const question = "A new complete factual question about reviewed history."
    document.querySelector<HTMLTextAreaElement>(".cnp-in")!.value = question
    f.control.fresh = async () => {
      const request = f.requests.find((item) => item.url.endsWith("/chat"))!
      const id = JSON.parse(String(request.init.body)).client_turn_id
      const response = citationFixture("1.1.0")
      response.turns[0].client_turn_id = id
      response.history.messages.forEach((message) => {
        message.client_turn_id = id
      })
      return json(response)
    }
    fireEvent.click(document.querySelector<HTMLButtonElement>(".cnp-send")!)
    await waitFor(() =>
      expect(
        f.requests.some((item) => item.url.endsWith("history-with-citations")),
      ).toBe(true),
    )
    const requests = f.requests.filter((item) => item.url.endsWith("/chat"))
    expect(requests).toHaveLength(1)
    const wire = String(requests[0].init.body),
      body = JSON.parse(wire)
    expect(Object.keys(body).sort()).toEqual([
      "client_turn_id",
      "message",
      "metadata",
    ])
    expect(body.message).toBe(question)
    expect(body.metadata).toEqual({
      output_format: "chat",
      clientId: "appointment_synthetic_a",
    })
    for (const excluded of [
      "reviewed_citations",
      "display_state",
      finalReference,
      "Synthetic identical recorded text.",
      "Synthetic recorded question",
      excerptAnswer,
    ])
      expect(wire).not.toContain(excluded)
    expect(Object.keys(body.metadata)).not.toContain("history")
    expect(
      f.requests.filter((item) => item.url.endsWith("history-with-citations")),
    ).toHaveLength(1)
  })

  it("uses a neutral historical label when a quoted answer has no verified accepted map", async () => {
    const value = citationFixture()
    value.history.messages[1].reviewed_citations = []
    value.history.messages[1].display_state = "historical"
    await fixture(async () => json(value))
    await screen.findByText("Historical answer.")
    await screen.findByText(
      "Reviewed-source links could not be verified. Attributed export is unavailable.",
    )
    expect(document.body.textContent).not.toContain(
      "reviewed-source attribution preserved",
    )
    expect(document.querySelector(".cnp-reviewed-citation")).toBeNull()
    expect(screen.queryByRole("button", { name: "Save answer .md" })).toBeNull()
  })

  it("restores superseded same-day identical quotes, original references and dated export without current-source reads", async () => {
    const f = await fixture()
    await waitFor(() =>
      expect(document.querySelectorAll(".cnp-reviewed-citation")).toHaveLength(
        2,
      ),
    )
    const links = Array.from(
      document.querySelectorAll<HTMLAnchorElement>(".cnp-reviewed-citation"),
    )
    expect(links.map((link) => link.href)).toEqual(
      [1, 2].map(
        (ordinal) =>
          `${window.location.origin}/session-prep/citations/${finalReference}.${ordinal}`,
      ),
    )
    expect(links.map((link) => link.textContent)).toEqual(
      expect.arrayContaining([expect.stringContaining("reviewed")]),
    )
    expect(
      links.every((link) => !link.textContent!.includes(finalReference)),
    ).toBe(true)
    expect(links[0].textContent).toContain("[1] Open reviewed source")
    expect(links[0].textContent).toContain("reviewed 2026-10-02 10:00:00 UTC")
    expect(links[1].textContent).toContain("reviewed 2026-10-02 11:00:00 UTC")
    expect(links[0].textContent).not.toBe(links[1].textContent)
    expect(document.body.textContent).toContain(
      "Historical answer · its reviewed sources have since changed",
    )
    expect(document.querySelectorAll(".cnp-evidence-body")[0].textContent).toBe(
      document.querySelectorAll(".cnp-evidence-body")[1].textContent,
    )
    const save = vi.fn((_blob: Blob | MediaSource) => "blob:synthetic-answer")
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = save
      },
    )
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {})
    fireEvent.click(screen.getByRole("button", { name: "Save answer .md" }))
    const exported = await (save.mock.calls[0][0] as Blob).text()
    expect(exported.startsWith(excerptAnswer)).toBe(true)
    expect(exported).toContain("Historical answer — sources superseded.")
    expect(exported).toContain(
      "Visit 2026-10-01T09:00:00Z; reviewed 2026-10-02T10:00:00Z",
    )
    expect(exported).toContain(`${finalReference}.1`)
    expect(exported).toContain(`${finalReference}.2`)
    expect(
      f.requests.filter((request) =>
        request.url.endsWith("/embed/history-retained"),
      ),
    ).toHaveLength(1)
    expect(
      f.requests.some((request) =>
        /\/chat$|history-with-citations|\/notes\//.test(request.url),
      ),
    ).toBe(false)
  })
  it.each(["exact", "different"])(
    "adds no live link before final read; handles the %s stored answer",
    async (mode) => {
      const empty = citationFixture()
      empty.history.messages = []
      empty.turns = []
      const f = await fixture(async () => json(empty))
      await waitFor(() =>
        expect(
          document.querySelector<HTMLButtonElement>(".cnp-send")?.disabled,
        ).toBe(false),
      )
      let resolve!: (value: Response) => void
      f.control.fresh = () =>
        new Promise<Response>((r) => {
          resolve = r
        })
      const input = document.querySelector<HTMLTextAreaElement>(".cnp-in")!
      input.value = "Original complete factual question."
      fireEvent.click(document.querySelector<HTMLButtonElement>(".cnp-send")!)
      await waitFor(() =>
        expect(
          f.requests.some((request) =>
            request.url.endsWith("history-with-citations"),
          ),
        ).toBe(true),
      )
      expect(document.querySelector(".cnp-reviewed-citation")).toBeNull()
      const fresh = citationFixture(
        "1.1.0",
        mode === "exact" ? excerptAnswer : "A different retained answer",
      )
      const id = JSON.parse(
        String(
          f.requests.find((request) => request.url.endsWith("/chat"))!.init
            .body,
        ),
      ).client_turn_id
      fresh.turns[0].client_turn_id = id
      fresh.history.messages.forEach((message) => {
        message.client_turn_id = id
      })
      await act(async () => resolve(json(fresh)))
      if (mode === "exact") {
        await waitFor(() =>
          expect(
            document.querySelectorAll(".cnp-reviewed-citation"),
          ).toHaveLength(2),
        )
        expect(
          f.requests.filter((request) => request.url.endsWith("/chat")),
        ).toHaveLength(1)
        return
      }
      await screen.findByText(
        "Reviewed-source links could not be verified. Attributed export is unavailable.",
      )
      expect(document.querySelector(".cnp-reviewed-citation")).toBeNull()
      expect(
        screen.queryByRole("button", { name: "Save answer .md" }),
      ).toBeNull()
      expect(document.body.textContent).not.toContain(
        "A different retained answer",
      )
    },
  )
  it("clears old maps on record change and discards a noncooperative late response", async () => {
    let resolve!: (value: Response) => void
    const f = await fixture(
      () =>
        new Promise<Response>((r) => {
          resolve = r
        }),
    )
    await waitFor(() =>
      expect(
        f.requests.some((request) => request.url.endsWith("history-retained")),
      ).toBe(true),
    )
    const current = citationFixture("1.2.0", "Current authorized record answer")
    current.history.messages[1].reviewed_citations = []
    f.control.retained = async () => json(current)
    f.view.rerender(<f.Host appointmentId="appointment_synthetic_b" />)
    await screen.findByText("Current authorized record answer")
    await act(async () => resolve(json(citationFixture())))
    expect(document.querySelector(".cnp-reviewed-citation")).toBeNull()
    expect(document.body.textContent).not.toContain(
      "Synthetic identical recorded text",
    )
    const before = f.requests.length
    await expect(
      f.mounts[1].loadCitationHistory("appointment_synthetic_a", {
        version: "1.2.0",
        mode: "recent",
        clientTurnId: null,
        signal: new AbortController().signal,
      }),
    ).rejects.toMatchObject({ name: "AbortError" })
    expect(f.requests).toHaveLength(before)
  })
  it.each([401, 403])(
    "denies retained display %s with no old answer or link",
    async (status) => {
      const f = await fixture(async () =>
        json({ private: "not-display" }, status),
      )
      await screen.findByRole("button", { name: "Reconnect assistant" })
      expect(document.querySelector(".cnp-root")).toBeNull()
      expect(document.body.textContent).not.toContain("not-display")
      expect(f.requests.some((request) => request.url.endsWith("/chat"))).toBe(
        false,
      )
    },
  )
})
