import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest"

import { getClient } from "~/configuration/clerk"

import {
  SOVEREIGN_PANEL_ASSET_PATH,
  SOVEREIGN_PANEL_INTEGRITY,
} from "./SovereignPanelAsset"
import {
  SovereignPanelHost,
  resolveSovereignProviderConfig,
} from "./SovereignPanelHost"
import { act, render, waitFor } from "@testing-library/react"

vi.mock("~/configuration/clerk", () => ({ getClient: vi.fn() }))

type PanelApi = NonNullable<Window["CosmaNeuraPanel"]>
type PanelOptions = Parameters<PanelApi["mount"]>[0]
type PanelInstance = ReturnType<PanelApi["mount"]>

let mountImplementation: ((options: PanelOptions) => PanelInstance) | null =
  null
const mount = vi.fn((options: PanelOptions) => {
  if (!mountImplementation) throw new Error("missing panel mount fixture")
  return mountImplementation(options)
})
const panelApi: PanelApi = { mount }
const RECORD_GRANT_A = `gA${"a".repeat(118)}`
const RECORD_GRANT_B = `gB${"b".repeat(118)}`
const RECORD_GRANT_STALE = `gS${"c".repeat(118)}`

function signedInClerk() {
  return {
    loaded: true,
    load: async () => {},
    user: { id: "user_provider_test" },
    session: {
      getToken: vi.fn(async (options?: { template?: string }) =>
        options?.template ? "provider-oidc-token" : "platform-session-token",
      ),
    },
    redirectToSignIn: async () => {},
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

describe("SovereignPanelHost", () => {
  beforeEach(() => {
    vi.stubEnv(
      "VITE_SOVEREIGN_PROVIDER_URL",
      "https://provider-sovereign.example",
    )
    vi.stubEnv("VITE_SOVEREIGN_CLERK_TEMPLATE", "cosmaneura-sovereign")
  })

  afterEach(() => {
    mountImplementation = null
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
    vi.restoreAllMocks()
  })

  afterAll(() => {
    delete window.CosmaNeuraPanel
  })

  it("accepts only an origin-only HTTPS target and a bounded template name", () => {
    expect(
      resolveSovereignProviderConfig({
        VITE_SOVEREIGN_PROVIDER_URL: "https://sovereign.example/",
        VITE_SOVEREIGN_CLERK_TEMPLATE: "cosmaneura-sovereign",
      }),
    ).toEqual({
      origin: "https://sovereign.example",
      clerkTemplate: "cosmaneura-sovereign",
    })

    for (const origin of [
      "http://sovereign.example",
      "https://user@sovereign.example",
      "https://sovereign.example/chat",
      "https://sovereign.example?mount=provider",
    ]) {
      expect(
        resolveSovereignProviderConfig({
          VITE_SOVEREIGN_PROVIDER_URL: origin,
          VITE_SOVEREIGN_CLERK_TEMPLATE: "cosmaneura-sovereign",
        }),
      ).toBeNull()
    }
    expect(
      resolveSovereignProviderConfig({
        VITE_SOVEREIGN_PROVIDER_URL: "https://sovereign.example",
        VITE_SOVEREIGN_CLERK_TEMPLATE: "bad template",
      }),
    ).toBeNull()
  })

  it("uses the Clerk template directly, rechecks scoped refs, and fences record races", async () => {
    const clerk = signedInClerk()
    vi.mocked(getClient).mockResolvedValue(clerk)

    let delayedResolve: (response: Response) => void = () => {
      throw new Error("delayed response was not requested")
    }
    let delayNext = false
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (delayNext) {
          delayNext = false
          return await new Promise<Response>((resolve) => {
            delayedResolve = resolve
          })
        }
        if (url.includes("appointment_a"))
          return json({ record_ref: RECORD_GRANT_A })
        if (url.includes("appointment_b"))
          return json({ record_ref: RECORD_GRANT_B })
        if (url.includes("appointment_c"))
          return json({ detail: "unavailable" }, 404)
        throw new Error(`unexpected URL ${url} ${String(init?.method)}`)
      },
    )
    vi.stubGlobal("fetch", fetchMock)

    const setRecord = vi.fn(async (_recordId: string) => {})
    const destroy = vi.fn()
    mountImplementation = () => ({ setRecord, destroy })
    const appendSpy = vi.spyOn(document.head, "appendChild")
    appendSpy.mockImplementation(<T extends Node>(node: T): T => {
      if (node instanceof HTMLScriptElement) {
        window.CosmaNeuraPanel = panelApi
        queueMicrotask(() => node.onload?.call(node, new Event("load")))
      }
      return node
    })

    const view = render(<SovereignPanelHost appointmentId="appointment_a" />)
    await waitFor(() => expect(mount).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(setRecord).toHaveBeenCalledWith("appointment_a"))

    const script = appendSpy.mock.calls[0][0] as HTMLScriptElement
    expect(script.getAttribute("src")).toBe(SOVEREIGN_PANEL_ASSET_PATH)
    expect(script.integrity).toBe(SOVEREIGN_PANEL_INTEGRITY)
    expect(script.crossOrigin).toBe("anonymous")

    const options = mount.mock.calls[0][0]
    expect(options.fabBottom).toBe("84px")
    await expect(options.getAuthToken()).resolves.toBe("provider-oidc-token")
    expect(clerk.session.getToken).toHaveBeenCalledWith({
      template: "cosmaneura-sovereign",
    })

    await expect(options.getContext()).resolves.toEqual({
      clientId: "appointment_a",
      record_ref: RECORD_GRANT_A,
    })
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/internal/v1/notes/appointment_a/ehr-labs-record",
      expect.objectContaining({
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: expect.objectContaining({
          Authorization: "Bearer platform-session-token",
        }),
      }),
    )

    view.rerender(<SovereignPanelHost appointmentId="appointment_b" />)
    await waitFor(() => expect(mount).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(setRecord).toHaveBeenCalledWith("appointment_b"))
    const optionsB = mount.mock.calls[1][0]
    await expect(optionsB.getContext()).resolves.toEqual({
      clientId: "appointment_b",
      record_ref: RECORD_GRANT_B,
    })

    delayNext = true
    const staleContext = optionsB.getContext()
    view.rerender(<SovereignPanelHost appointmentId="appointment_c" />)
    await waitFor(() => expect(mount).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(setRecord).toHaveBeenCalledWith("appointment_c"))
    delayedResolve(json({ record_ref: RECORD_GRANT_STALE }))
    await expect(staleContext).rejects.toThrow("Open record changed")

    const optionsC = mount.mock.calls[2][0]
    await expect(optionsC.getContext()).resolves.toEqual({
      clientId: "appointment_c",
    })
    const serializedContext = JSON.stringify(await optionsC.getContext())
    expect(serializedContext).not.toContain("name")
    expect(serializedContext).not.toContain("chart")
    expect(serializedContext).not.toContain("date_of_birth")

    view.unmount()
    expect(destroy).toHaveBeenCalledTimes(3)
  })

  it("destroys and detaches the panel when the initial record bind fails", async () => {
    vi.mocked(getClient).mockResolvedValue(signedInClerk())
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ record_ref: RECORD_GRANT_A })),
    )
    const destroy = vi.fn()
    const setRecord = vi.fn(async () => {
      throw new Error("bind failed")
    })
    mountImplementation = () => ({ setRecord, destroy })
    vi.spyOn(document.head, "appendChild").mockImplementation(
      <T extends Node>(node: T): T => {
        if (node instanceof HTMLScriptElement) {
          window.CosmaNeuraPanel = panelApi
          queueMicrotask(() => node.onload?.call(node, new Event("load")))
        }
        return node
      },
    )

    render(<SovereignPanelHost appointmentId="appointment_a" />)
    await waitFor(() => expect(setRecord).toHaveBeenCalledWith("appointment_a"))
    await waitFor(() => expect(destroy).toHaveBeenCalledTimes(1))
  })

  it("destroys an obsolete panel exactly once when its initial bind resolves late", async () => {
    vi.mocked(getClient).mockResolvedValue(signedInClerk())
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ record_ref: RECORD_GRANT_A })),
    )

    let finishFirstBind: () => void = () => {
      throw new Error("first bind was not started")
    }
    const firstSetRecord = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishFirstBind = resolve
        }),
    )
    const firstDestroy = vi.fn()
    const secondSetRecord = vi.fn(async () => {})
    const secondDestroy = vi.fn()
    let mountCount = 0
    mountImplementation = () => {
      mountCount += 1
      return mountCount === 1
        ? { setRecord: firstSetRecord, destroy: firstDestroy }
        : { setRecord: secondSetRecord, destroy: secondDestroy }
    }
    vi.spyOn(document.head, "appendChild").mockImplementation(
      <T extends Node>(node: T): T => {
        if (node instanceof HTMLScriptElement) {
          window.CosmaNeuraPanel = panelApi
          queueMicrotask(() => node.onload?.call(node, new Event("load")))
        }
        return node
      },
    )

    const view = render(<SovereignPanelHost appointmentId="appointment_a" />)
    await waitFor(() =>
      expect(firstSetRecord).toHaveBeenCalledWith("appointment_a"),
    )

    view.rerender(<SovereignPanelHost appointmentId="appointment_b" />)
    await waitFor(() =>
      expect(secondSetRecord).toHaveBeenCalledWith("appointment_b"),
    )
    expect(firstDestroy).toHaveBeenCalledTimes(1)

    await act(async () => finishFirstBind())
    expect(firstDestroy).toHaveBeenCalledTimes(1)
    expect(secondDestroy).not.toHaveBeenCalled()

    view.unmount()
    expect(secondDestroy).toHaveBeenCalledTimes(1)
  })
})
