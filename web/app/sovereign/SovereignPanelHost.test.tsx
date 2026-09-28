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
import { getViewAsStatus } from "~/lib/view-as-api"

import {
  SovereignPanelHost,
  resolveSovereignProviderConfig,
} from "./SovereignPanelHost"
import { act, render, waitFor } from "@testing-library/react"

vi.mock("~/configuration/clerk", () => ({ getClient: vi.fn() }))
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

function signedInClerk() {
  return {
    loaded: true,
    load: async () => {},
    user: { id: "user_provider_test" },
    session: {
      getToken: vi.fn(
        async (options?: { template?: string }): Promise<string> =>
          options?.template ? "provider-oidc-token" : "platform-session-token",
      ),
    },
    redirectToSignIn: async () => {},
  }
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

function mountContextResponse(contextToken: string) {
  const now = Date.now()
  return {
    context_token: contextToken,
    token_type: "Bearer",
    issued_at: new Date(now - 1_000).toISOString(),
    refresh_after: new Date(now + 5 * 60_000).toISOString(),
    expires_at: new Date(now + 15 * 60_000).toISOString(),
  }
}

describe("SovereignPanelHost", () => {
  beforeEach(() => {
    vi.stubEnv(
      "VITE_SOVEREIGN_PROVIDER_URL",
      "https://provider-sovereign.example",
    )
    vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true")
    vi.mocked(getViewAsStatus).mockResolvedValue(inactiveViewAs)
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

  it("accepts only an enabled, origin-only HTTPS authority target", () => {
    expect(
      resolveSovereignProviderConfig({
        VITE_SOVEREIGN_PROVIDER_URL: "https://sovereign.example/",
        VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED: "true",
      }),
    ).toEqual({
      origin: "https://sovereign.example",
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
          VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED: "true",
        }),
      ).toBeNull()
    }
    for (const flag of [undefined, "false", "TRUE", true]) {
      expect(
        resolveSovereignProviderConfig({
          VITE_SOVEREIGN_PROVIDER_URL: "https://sovereign.example",
          VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED: flag,
        }),
      ).toBeNull()
    }
  })

  it("never loads or mounts Sovereign while view-as is active", async () => {
    vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true")
    vi.mocked(getViewAsStatus).mockResolvedValue({
      ...inactiveViewAs,
      request_binding:
        "vab1.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" as const,
      can_view_as: true,
      active: true,
      read_only: true,
      expires_at: "2099-08-24T17:15:00Z",
      actor: {
        doctor_id: "doctor_actor",
        email: "actor@example.com",
      },
      target: {
        doctor_id: "doctor_target",
        email: "target@example.com",
      },
    })
    const appendChild = vi.spyOn(document.head, "appendChild")

    render(<SovereignPanelHost appointmentId="appointment_view_as" />)
    await waitFor(() => expect(getViewAsStatus).toHaveBeenCalledTimes(1))

    expect(mount).not.toHaveBeenCalled()
    expect(
      appendChild.mock.calls.some(
        ([node]) => node instanceof HTMLScriptElement,
      ),
    ).toBe(false)
  })

  it("reuses and rotates one appointment-bound mount session when enabled", async () => {
    vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true")
    let now = Date.UTC(2026, 7, 20, 12, 0, 0)
    vi.spyOn(Date, "now").mockImplementation(() => now)
    const clerk = signedInClerk()
    vi.mocked(getClient).mockResolvedValue(clerk)
    const mountContext = `mctx_${"a".repeat(48)}`
    const rotatedContext = `mctx_${"b".repeat(48)}`
    const scheduledContext = `mctx_${"e".repeat(48)}`
    const nextRecordContext = `mctx_${"d".repeat(48)}`
    let exchangeCount = 0
    let rotationCount = 0
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input)
        if (url === "/embed/v1/context-tokens/exchange") {
          exchangeCount += 1
          return json(
            mountContextResponse(
              exchangeCount === 1 ? mountContext : nextRecordContext,
            ),
          )
        }
        if (url === "/embed/v1/context-tokens/rotate") {
          rotationCount += 1
          return json(
            mountContextResponse(
              rotationCount === 1 ? rotatedContext : scheduledContext,
            ),
          )
        }
        throw new Error(`unexpected URL ${url}`)
      },
    )
    vi.stubGlobal("fetch", fetchMock)
    const open = vi.spyOn(window, "open").mockReturnValue({} as Window)
    const setRecord = vi.fn(async () => 1)
    mountImplementation = () => ({ setRecord, destroy: vi.fn() })
    vi.spyOn(document.head, "appendChild").mockImplementation(
      <T extends Node>(node: T): T => {
        if (node instanceof HTMLScriptElement) {
          window.CosmaNeuraPanel = panelApi
          queueMicrotask(() => node.onload?.call(node, new Event("load")))
        }
        return node
      },
    )

    const view = render(
      <SovereignPanelHost appointmentId="appointment_synthetic_001" />,
    )
    await waitFor(() => expect(mount).toHaveBeenCalledTimes(1))
    const options = mount.mock.calls[0][0]
    expect(options.embedVersion).toBe("1.0")
    expect(options.fabBottom).toBe("84px")
    expect(options.shiftContent).toBe(true)
    expect(options).not.toHaveProperty("fabRight")
    const layerStyle = document.querySelector(
      "style[data-sovereign-provider-v7-layer]",
    )
    expect(layerStyle?.textContent).toContain(
      ".cnp-root > .cnp-rail { z-index: 100001 !important; }",
    )
    expect(layerStyle?.textContent).toContain(
      ".cnp-root > .cnp-tip { z-index: 100002 !important; }",
    )
    expect(options).not.toHaveProperty("requireClientTurnId")
    expect(options.loadHistory).toBeTypeOf("function")
    await expect(
      Promise.all([options.getAuthToken(), options.getAuthToken()]),
    ).resolves.toEqual([mountContext, mountContext])
    await expect(options.getAuthToken()).resolves.toBe(mountContext)
    await expect(options.getAuthToken({ forceRefresh: true })).resolves.toBe(
      rotatedContext,
    )
    await expect(options.getAuthToken()).resolves.toBe(rotatedContext)
    now += 6 * 60_000
    await expect(options.getAuthToken()).resolves.toBe(scheduledContext)
    await expect(options.getAuthToken()).resolves.toBe(scheduledContext)
    await expect(options.getContext()).resolves.toEqual({
      clientId: "appointment_synthetic_001",
    })

    const exchanges = fetchMock.mock.calls.filter(
      ([input]) => String(input) === "/embed/v1/context-tokens/exchange",
    )
    expect(exchanges).toHaveLength(1)
    expect(exchanges[0][1]).toEqual(
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: expect.objectContaining({
          Authorization: "Bearer platform-session-token",
          "X-Cosmaneura-Embed-Version": "1.0",
        }),
        body: JSON.stringify({
          requested_surface: "provider_assistant",
          record_ref: "appointment_synthetic_001",
        }),
      }),
    )
    const rotations = fetchMock.mock.calls.filter(
      ([input]) => String(input) === "/embed/v1/context-tokens/rotate",
    )
    expect(rotations).toHaveLength(2)
    expect(rotations[0][1]).toEqual(
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: expect.objectContaining({
          Authorization: `Bearer ${mountContext}`,
          "X-Cosmaneura-Embed-Version": "1.0",
        }),
        body: JSON.stringify({
          requested_surface: "provider_assistant",
          record_ref: "appointment_synthetic_001",
        }),
      }),
    )
    expect(rotations[1][1]).toEqual(
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: `Bearer ${rotatedContext}`,
        }),
      }),
    )
    expect(clerk.session.getToken).toHaveBeenCalledTimes(1)
    expect(clerk.session.getToken).toHaveBeenCalledWith()
    expect(
      clerk.session.getToken.mock.calls.every((args) => args.length === 0),
    ).toBe(true)
    options.onHumanContact({
      label: "Talk with a person",
      action_id: "human-contact",
      fallback_text: "Contact support.",
    })
    expect(open).toHaveBeenCalledWith(
      "/support#contact",
      "_blank",
      "noopener,noreferrer",
    )
    expect(() =>
      options.onHumanContact({
        label: "Unsafe",
        action_id: "unreviewed-action",
        fallback_text: "Do not navigate.",
      }),
    ).toThrow("Human contact action is unavailable")

    view.rerender(
      <SovereignPanelHost appointmentId="appointment_synthetic_003" />,
    )
    await waitFor(() => expect(mount).toHaveBeenCalledTimes(2))
    await expect(options.getAuthToken()).rejects.toThrow(
      "Provider authority is unavailable",
    )
    const nextOptions = mount.mock.calls[1][0]
    await expect(nextOptions.getAuthToken()).resolves.toBe(nextRecordContext)
    expect(
      fetchMock.mock.calls.filter(
        ([input]) => String(input) === "/embed/v1/context-tokens/exchange",
      ),
    ).toHaveLength(2)
    expect(fetchMock.mock.calls.at(-1)?.[1]).toEqual(
      expect.objectContaining({
        body: JSON.stringify({
          requested_surface: "provider_assistant",
          record_ref: "appointment_synthetic_003",
        }),
      }),
    )
  })

  it("fails closed after rotation authorization is rejected", async () => {
    vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "true")
    const now = Date.UTC(2026, 7, 20, 12, 0, 0)
    vi.spyOn(Date, "now").mockImplementation(() => now)
    vi.mocked(getClient).mockResolvedValue(signedInClerk())
    const mountContext = `mctx_${"c".repeat(48)}`
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === "/embed/v1/context-tokens/exchange") {
        return json(mountContextResponse(mountContext))
      }
      if (url === "/embed/v1/context-tokens/rotate") {
        return json({ code: "TOKEN_INVALID" }, 401)
      }
      throw new Error(`unexpected URL ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    mountImplementation = () => ({
      setRecord: vi.fn(async () => 1),
      destroy: vi.fn(),
    })
    vi.spyOn(document.head, "appendChild").mockImplementation(
      <T extends Node>(node: T): T => {
        if (node instanceof HTMLScriptElement) {
          window.CosmaNeuraPanel = panelApi
          queueMicrotask(() => node.onload?.call(node, new Event("load")))
        }
        return node
      },
    )

    render(<SovereignPanelHost appointmentId="appointment_synthetic_002" />)
    await waitFor(() => expect(mount).toHaveBeenCalledTimes(1))
    const options = mount.mock.calls[0][0]
    await expect(options.getAuthToken()).resolves.toBe(mountContext)
    await expect(options.getAuthToken({ forceRefresh: true })).rejects.toThrow(
      "Provider authority is unavailable",
    )
    await expect(options.getAuthToken()).rejects.toThrow(
      "Provider authority is unavailable",
    )

    expect(
      fetchMock.mock.calls.filter(
        ([input]) => String(input) === "/embed/v1/context-tokens/exchange",
      ),
    ).toHaveLength(1)
    expect(
      fetchMock.mock.calls.filter(
        ([input]) => String(input) === "/embed/v1/context-tokens/rotate",
      ),
    ).toHaveLength(1)
  })

  it("remounts synchronously and exposes only the record binding", async () => {
    vi.mocked(getClient).mockResolvedValue(signedInClerk())
    const firstDestroy = vi.fn()
    const secondDestroy = vi.fn()
    const firstSetRecord = vi.fn(async () => 1)
    const secondSetRecord = vi.fn(async () => 1)
    const panels = [
      { setRecord: firstSetRecord, destroy: firstDestroy },
      { setRecord: secondSetRecord, destroy: secondDestroy },
    ]
    let panelIndex = 0
    mountImplementation = () => panels[panelIndex++]
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
    await waitFor(() => expect(mount).toHaveBeenCalledTimes(1))
    const firstOptions = mount.mock.calls[0][0]
    await expect(firstOptions.getContext()).resolves.toEqual({
      clientId: "appointment_a",
    })

    view.rerender(<SovereignPanelHost appointmentId="appointment_b" />)
    // Layout-effect cleanup must detach A before rerender can paint B.
    expect(firstSetRecord).toHaveBeenCalledWith(null)
    expect(firstDestroy).toHaveBeenCalledTimes(1)
    await expect(firstOptions.getContext()).rejects.toThrow(
      "Open record changed while context was resolving",
    )
    await waitFor(() => expect(mount).toHaveBeenCalledTimes(2))
    const secondOptions = mount.mock.calls[1][0]
    await expect(secondOptions.getContext()).resolves.toEqual({
      clientId: "appointment_b",
    })

    const browserPersistence = [window.localStorage, window.sessionStorage]
      .flatMap((storage) =>
        Array.from({ length: storage.length }, (_, index) =>
          storage.getItem(storage.key(index) ?? ""),
        ),
      )
      .join("|")
    const serverOnlyRef = "context_synthetic_must_stay_server_side"
    expect(window.location.href).not.toContain(serverOnlyRef)
    expect(document.documentElement.outerHTML).not.toContain(serverOnlyRef)
    expect(browserPersistence).not.toContain(serverOnlyRef)
  })

  it("rejects an exchange response that echoes a Clerk-shaped credential", async () => {
    const clerkToken = `eyJ.${"a".repeat(48)}.${"b".repeat(48)}`
    const clerk = signedInClerk()
    clerk.session.getToken.mockResolvedValue(clerkToken)
    vi.mocked(getClient).mockResolvedValue(clerk)
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(mountContextResponse(clerkToken))),
    )
    mountImplementation = () => ({
      setRecord: vi.fn(async () => 1),
      destroy: vi.fn(),
    })
    vi.spyOn(document.head, "appendChild").mockImplementation(
      <T extends Node>(node: T): T => {
        if (node instanceof HTMLScriptElement) {
          window.CosmaNeuraPanel = panelApi
          queueMicrotask(() => node.onload?.call(node, new Event("load")))
        }
        return node
      },
    )

    render(<SovereignPanelHost appointmentId="appointment_synthetic_echo" />)
    await waitFor(() => expect(mount).toHaveBeenCalledTimes(1))

    await expect(mount.mock.calls[0][0].getAuthToken()).rejects.toThrow(
      "Provider authority response is invalid",
    )
    expect(clerk.session.getToken).toHaveBeenCalledWith()
  })

  it("does not load or mount the panel when platform authority is disabled", async () => {
    vi.stubEnv("VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "false")
    const clerk = signedInClerk()
    vi.mocked(getClient).mockResolvedValue(clerk)
    const fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)

    const appendSpy = vi.spyOn(document.head, "appendChild")

    render(<SovereignPanelHost appointmentId="appointment_a" />)
    await act(async () => undefined)

    expect(getViewAsStatus).not.toHaveBeenCalled()
    expect(getClient).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(mount).not.toHaveBeenCalled()
    expect(
      appendSpy.mock.calls.some(([node]) => node instanceof HTMLScriptElement),
    ).toBe(false)
    expect(
      document.querySelector("style[data-sovereign-provider-v7-layer]"),
    ).toBeNull()
  })

  it("destroys and detaches the panel when the initial record bind fails", async () => {
    vi.mocked(getClient).mockResolvedValue(signedInClerk())
    const destroy = vi.fn()
    const setRecord = vi.fn(async (recordId: string | null) => {
      if (recordId === null) return 1
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
    expect(setRecord).toHaveBeenCalledWith(null)
  })

  it("destroys an obsolete panel exactly once when its initial bind resolves late", async () => {
    vi.mocked(getClient).mockResolvedValue(signedInClerk())

    let finishFirstBind: () => void = () => {
      throw new Error("first bind was not started")
    }
    const firstSetRecord = vi.fn((recordId: string | null) => {
      if (recordId === null) return Promise.resolve(1)
      return new Promise<void>((resolve) => {
        finishFirstBind = resolve
      })
    })
    const firstDestroy = vi.fn()
    const secondSetRecord = vi.fn(async () => 1)
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
    expect(firstSetRecord).toHaveBeenCalledWith(null)

    await act(async () => finishFirstBind())
    expect(firstDestroy).toHaveBeenCalledTimes(1)
    expect(secondDestroy).not.toHaveBeenCalled()

    view.unmount()
    expect(secondDestroy).toHaveBeenCalledTimes(1)
    expect(secondSetRecord).toHaveBeenCalledWith(null)
  })
})
