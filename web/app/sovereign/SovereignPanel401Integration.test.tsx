import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, describe, expect, it, vi } from "vitest"

import { getClient } from "~/configuration/clerk"
import { getViewAsStatus } from "~/lib/view-as-api"

import { SOVEREIGN_PANEL_ASSET_PATH } from "./SovereignPanelAsset"
import { SovereignPanelHost } from "./SovereignPanelHost"
import { render, waitFor } from "@testing-library/react"

vi.mock("~/configuration/clerk", () => ({
  getClient: vi.fn(),
  getSessionToken: vi.fn(
    async (
      options: { template?: string } | undefined,
      client: {
        session: {
          getToken: (options?: { template?: string }) => Promise<string | null>
        }
      },
    ) =>
      options ? client.session.getToken(options) : client.session.getToken(),
  ),
}))
vi.mock("~/lib/view-as-api", () => ({ getViewAsStatus: vi.fn() }))

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

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "X-Cosmaneura-Embed-Version": "1.0",
    },
  })
}

function contextTokenResponse(contextToken: string) {
  const now = Date.now()
  return {
    context_token: contextToken,
    token_type: "Bearer",
    issued_at: new Date(now - 1_000).toISOString(),
    refresh_after: new Date(now + 5 * 60_000).toISOString(),
    expires_at: new Date(now + 15 * 60_000).toISOString(),
  }
}

describe("Sovereign panel 401 retry integration", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    delete window.CosmaNeuraPanel
    document.body.innerHTML = ""
    document.head
      .querySelectorAll("style")
      .forEach((element) => element.remove())
  })

  it("rotates once and preserves the exact turn within one mount session", async () => {
    vi.stubEnv(
      "VITE_SOVEREIGN_PROVIDER_URL",
      "https://provider-sovereign.example",
    )
    vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true")
    vi.mocked(getViewAsStatus).mockResolvedValue(inactiveViewAs)
    vi.mocked(getClient).mockResolvedValue({
      loaded: true,
      load: async () => {},
      user: { id: "user_provider_test" },
      session: {
        getToken: vi.fn(async () => "platform-session-token"),
      },
      redirectToSignIn: async () => {},
    })

    const firstToken = `mctx_${"a".repeat(48)}`
    const rotatedToken = `mctx_${"b".repeat(48)}`
    const chatRequests: Array<{
      authorization: string | null
      body: string
      clientTurnId: string | null
    }> = []
    let exchanges = 0
    let rotations = 0

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url === "/embed/v1/context-tokens/exchange") {
          exchanges += 1
          return json(contextTokenResponse(firstToken), 201)
        }
        if (url === "/embed/v1/context-tokens/rotate") {
          rotations += 1
          expect(new Headers(init?.headers).get("authorization")).toBe(
            `Bearer ${firstToken}`,
          )
          return json(contextTokenResponse(rotatedToken), 201)
        }
        if (url === "https://provider-sovereign.example/welcome") {
          return json({
            partner_name: "Synthetic provider",
            persona: "provider",
            greeting: "Welcome",
            paths: [],
            disclaimer: "Synthetic test only.",
            emergency_note: "Call emergency services when needed.",
          })
        }
        if (url === "https://provider-sovereign.example/chat") {
          const headers = new Headers(init?.headers)
          chatRequests.push({
            authorization: headers.get("authorization"),
            body: String(init?.body),
            clientTurnId: headers.get("x-cosmaneura-client-turn-id"),
          })
          if (chatRequests.length === 1) {
            return json(
              {
                type: "https://contracts.cosmaneura.com/problems/token_invalid",
                title: "Token invalid",
                status: 401,
                code: "TOKEN_INVALID",
                detail: "The assistant session could not be verified.",
                trace_id: "trace_synthetic_auth_retry",
                retryable: false,
              },
              401,
            )
          }
          const request = JSON.parse(String(init?.body)) as {
            client_turn_id: string
          }
          return json({
            answer: "Synthetic governed answer",
            intent: "small_talk",
            client_turn_id: request.client_turn_id,
            citations: [],
            doctrinal_review: null,
            structured: {},
            safety_notice: null,
            presentation: {
              mode: "standard",
              suppress: [],
              human_contact: null,
            },
          })
        }
        throw new Error(`unexpected URL ${url}`)
      }),
    )

    const panelSource = readFileSync(
      join(process.cwd(), "public", SOVEREIGN_PANEL_ASSET_PATH),
      "utf8",
    )
    vi.spyOn(document.head, "appendChild").mockImplementation(
      <T extends Node>(node: T): T => {
        if (node instanceof HTMLScriptElement) {
          window.eval(panelSource)
          queueMicrotask(() => node.onload?.call(node, new Event("load")))
        }
        return node
      },
    )

    const view = render(
      <SovereignPanelHost appointmentId="appointment_synthetic_401" />,
    )
    await waitFor(() =>
      expect(
        document.querySelector<HTMLButtonElement>(".cnp-send")?.disabled,
      ).toBe(false),
    )
    const input = document.querySelector<HTMLTextAreaElement>(".cnp-in")
    const send = document.querySelector<HTMLButtonElement>(".cnp-send")
    expect(input).not.toBeNull()
    expect(send).not.toBeNull()
    input!.value = "synthetic exact retry"
    send!.click()

    await waitFor(() =>
      expect(document.querySelector(".cnp-retry")).not.toBeNull(),
    )
    document.querySelector<HTMLButtonElement>(".cnp-retry")!.click()
    await waitFor(() => expect(chatRequests).toHaveLength(2))

    expect(exchanges).toBe(1)
    expect(rotations).toBe(1)
    expect(chatRequests[0].authorization).toBe(`Bearer ${firstToken}`)
    expect(chatRequests[1].authorization).toBe(`Bearer ${rotatedToken}`)
    expect(chatRequests[1].body).toBe(chatRequests[0].body)
    expect(chatRequests[1].clientTurnId).toBe(chatRequests[0].clientTurnId)
    expect(JSON.parse(chatRequests[0].body).client_turn_id).toBe(
      chatRequests[0].clientTurnId,
    )

    view.unmount()
  })
})
