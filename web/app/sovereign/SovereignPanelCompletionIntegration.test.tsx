import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, expect, it, vi } from "vitest"

import { getClient } from "~/configuration/clerk"
import { getViewAsStatus } from "~/lib/view-as-api"

import { SOVEREIGN_PANEL_ASSET_PATH } from "./SovereignPanelAsset"
import { SovereignPanelHost } from "./SovereignPanelHost"
import { act, render, waitFor } from "@testing-library/react"

vi.mock("~/configuration/clerk", () => ({ getClient: vi.fn() }))
vi.mock("~/lib/view-as-api", () => ({ getViewAsStatus: vi.fn() }))

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  delete window.CosmaNeuraPanel
  document.head.querySelectorAll("style").forEach((node) => node.remove())
})

it("loads the pinned dashboard asset and completes preparation without a second click", async () => {
  vi.stubEnv(
    "VITE_SOVEREIGN_PROVIDER_URL",
    "https://provider-sovereign.example",
  )
  vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true")
  vi.spyOn(document, "hidden", "get").mockReturnValue(false)
  vi.mocked(getViewAsStatus).mockResolvedValue({
    request_binding: "inactive",
    can_view_as: false,
    active: false,
    read_only: false,
    diagnostic_code: null,
    expires_at: null,
    actor: null,
    target: null,
  })
  vi.mocked(getClient).mockResolvedValue({
    loaded: true,
    load: async () => {},
    user: { id: "user_provider_test" },
    session: { getToken: async () => "synthetic-platform-token" },
    redirectToSignIn: async () => {},
  })
  const token = `mctx_${"a".repeat(48)}`
  const turns: string[] = []
  let exchanges = 0
  function json(body: unknown, status = 200) {
    return new Response(JSON.stringify(body), {
      status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Cosmaneura-Embed-Version": "1.0",
      },
    })
  }
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === "/embed/v1/context-tokens/exchange") {
        exchanges += 1
        expect(JSON.parse(String(init?.body)).record_ref).toBe(
          "appointment_synthetic_completion",
        )
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
          partner_name: "Synthetic",
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
      expect(url).toBe("https://provider-sovereign.example/session-prep")
      expect(new Headers(init?.headers).get("authorization")).toBe(
        `Bearer ${token}`,
      )
      const body = JSON.parse(String(init?.body))
      expect(Object.keys(body)).toEqual(["client_turn_id"])
      expect(
        new Headers(init?.headers).get("x-cosmaneura-client-turn-id"),
      ).toBe(body.client_turn_id)
      turns.push(body.client_turn_id)
      const ready = turns.length === 2
      return json({
        client_turn_id: body.client_turn_id,
        state: ready ? "ready" : "pending",
        freshness: ready ? "current" : "none",
        current_through: ready ? "2026-09-24T12:00:00Z" : null,
        source_count: ready ? 1 : 0,
        prepared_brief: ready
          ? {
              recap: "Synthetic completed brief",
              trajectory_and_material_changes: [],
              talking_points: ["Review interval changes"],
              open_threads: [],
              cautions: [],
              suggested_outline: ["Discuss prior plan"],
              patient_reported: null,
            }
          : null,
        retry_after_seconds: ready ? null : 5,
        reason_code: ready ? null : "generation_pending",
      })
    }),
  )
  const panelSource = readFileSync(
    join(process.cwd(), "public", SOVEREIGN_PANEL_ASSET_PATH),
    "utf8",
  )
  vi.spyOn(document.head, "appendChild").mockImplementation(
    <T extends Node>(node: T): T => {
      if (node instanceof HTMLScriptElement) {
        expect(node.getAttribute("src")).toBe(SOVEREIGN_PANEL_ASSET_PATH)
        window.eval(panelSource)
        queueMicrotask(() => node.onload?.call(node, new Event("load")))
      }
      return node
    },
  )
  const view = render(
    <SovereignPanelHost appointmentId="appointment_synthetic_completion" />,
  )
  await waitFor(() =>
    expect(
      document.querySelector<HTMLButtonElement>(".cnp-send")?.disabled,
    ).toBe(false),
  )
  document.querySelector<HTMLButtonElement>(".cnp-fab")!.click()
  vi.useFakeTimers()
  document.querySelector<HTMLButtonElement>(".cnp-prep-go")!.focus()
  document.querySelector<HTMLButtonElement>(".cnp-prep-go")!.click()
  await act(() => vi.advanceTimersByTimeAsync(0))
  expect(document.querySelector(".cnp-prep-view")?.textContent).toContain(
    "Session Prep pending",
  )
  await act(() => vi.advanceTimersByTimeAsync(5000))
  expect(document.querySelector(".cnp-prep-view")?.textContent).toContain(
    "Synthetic completed brief",
  )
  expect(turns).toHaveLength(2)
  expect(new Set(turns).size).toBe(2)
  expect(exchanges).toBe(1)
  expect(
    document.querySelector(".cnp-prep-view")?.hasAttribute("aria-live"),
  ).toBe(false)
  expect(
    document.querySelector(".cnp-prep-progress")?.getAttribute("aria-live"),
  ).toBe("polite")
  // A second pending run is cancelled by the actual React host's cleanup.
  document.querySelector<HTMLButtonElement>(".cnp-prep-go")!.focus()
  document.querySelector<HTMLButtonElement>(".cnp-prep-go")!.click()
  await act(() => vi.advanceTimersByTimeAsync(0))
  expect(turns).toHaveLength(3)
  view.unmount()
  await act(() => vi.advanceTimersByTimeAsync(20000))
  expect(turns).toHaveLength(3)
  expect(document.querySelector(".cnp-prep-view")).toBeNull()
})
