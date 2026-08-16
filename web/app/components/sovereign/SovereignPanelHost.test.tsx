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
  SovereignPanelHost,
  resolveSovereignPanelConfig,
  sovereignPanelScriptUrl,
} from "./SovereignPanelHost"
import { render, waitFor } from "@testing-library/react"

vi.mock("~/configuration/clerk", () => ({ getClient: vi.fn() }))

type PanelApi = NonNullable<Window["CosmaNeuraPanel"]>
type PanelMountOptions = Parameters<PanelApi["mount"]>[0]
type PanelInstance = ReturnType<PanelApi["mount"]>

let versionedMountImplementation:
  | ((options: PanelMountOptions) => PanelInstance)
  | null = null
const versionedMount = vi.fn((options: PanelMountOptions) => {
  if (!versionedMountImplementation) {
    throw new Error("Versioned panel test mount is not configured")
  }
  return versionedMountImplementation(options)
})
const versionedPanelApi: PanelApi = { mount: versionedMount }

function serveVersionedPanelAsset(panelApi: PanelApi) {
  const appendSpy = vi.spyOn(document.head, "appendChild")
  const append = <T extends Node>(node: T): T => {
    if (
      node instanceof HTMLScriptElement &&
      node.dataset.cosmaneuraSovereignPanel === "v1"
    ) {
      window.CosmaNeuraPanel = panelApi
      queueMicrotask(() => node.onload?.call(node, new Event("load")))
    }
    return node
  }
  appendSpy.mockImplementation(append)
  return appendSpy
}

function signedInClient(token: string | null = "provider-token") {
  return {
    loaded: true,
    load: async () => {},
    user: { id: "provider_1" },
    session: {
      getToken: vi.fn(async (_options?: { template?: string }) => token),
    },
    redirectToSignIn: async () => {},
  }
}

describe("SovereignPanelHost", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_SOVEREIGN_URL", "https://provider-sovereign.example")
    vi.stubEnv("VITE_SOVEREIGN_CLERK_JWT_TEMPLATE", "sovereign-provider")
  })

  afterEach(() => {
    versionedMountImplementation = null
    vi.unstubAllEnvs()
    vi.clearAllMocks()
    vi.restoreAllMocks()
  })

  afterAll(() => {
    delete window.CosmaNeuraPanel
  })

  it("requires an origin-only HTTPS URL and an explicit Clerk template", () => {
    expect(
      resolveSovereignPanelConfig({
        VITE_SOVEREIGN_URL: "https://sovereign.example/",
        VITE_SOVEREIGN_CLERK_JWT_TEMPLATE: " provider-mount ",
      }),
    ).toEqual({
      origin: "https://sovereign.example",
      clerkJwtTemplate: "provider-mount",
    })

    for (const url of [
      "http://sovereign.example",
      "https://user@sovereign.example",
      "https://sovereign.example/another-path",
      "https://sovereign.example?mount=provider",
    ]) {
      expect(
        resolveSovereignPanelConfig({
          VITE_SOVEREIGN_URL: url,
          VITE_SOVEREIGN_CLERK_JWT_TEMPLATE: "provider-mount",
        }),
      ).toBeNull()
    }

    expect(
      resolveSovereignPanelConfig({
        VITE_SOVEREIGN_URL: "https://sovereign.example",
        VITE_SOVEREIGN_CLERK_JWT_TEMPLATE: "",
      }),
    ).toBeNull()
    expect(sovereignPanelScriptUrl("https://sovereign.example")).toBe(
      "https://sovereign.example/embed/v1/panel.js",
    )
  })

  it("refuses a pre-existing panel global that it did not load", async () => {
    const clerk = signedInClient()
    vi.mocked(getClient).mockResolvedValue(clerk)

    const mount = vi.fn((_options: PanelMountOptions) => ({
      setRecord: vi.fn(),
      destroy: vi.fn(),
    }))
    window.CosmaNeuraPanel = { mount }

    render(<SovereignPanelHost recordId="patient_local_a" />)

    await waitFor(() => expect(clerk.session.getToken).toHaveBeenCalled())
    expect(mount).not.toHaveBeenCalled()
    expect(
      document.querySelector("script[data-cosmaneura-sovereign-panel]"),
    ).toBeNull()
    delete window.CosmaNeuraPanel
  })

  it("removes a failed versioned asset and clears its handlers", async () => {
    const clerk = signedInClient()
    vi.mocked(getClient).mockResolvedValue(clerk)
    const detachedHead = document.createDocumentFragment()
    let script: HTMLScriptElement | null = null
    const appendSpy = vi.spyOn(document.head, "appendChild")
    const append = <T extends Node>(node: T): T => {
      detachedHead.appendChild(node)
      if (node instanceof HTMLScriptElement) {
        script = node
        queueMicrotask(() => node.onerror?.call(node, new Event("error")))
      }
      return node
    }
    appendSpy.mockImplementation(append)

    render(<SovereignPanelHost recordId="patient_local_a" />)

    await waitFor(() => expect(script).not.toBeNull())
    await waitFor(() => expect(script!.parentNode).toBeNull())
    expect(script!.onload).toBeNull()
    expect(script!.onerror).toBeNull()
    expect(window.CosmaNeuraPanel).toBeUndefined()
  })

  it("destroys the rail when initial record binding fails", async () => {
    const clerk = signedInClient()
    vi.mocked(getClient).mockResolvedValue(clerk)

    const setRecord = vi.fn(async (_recordId: string) => {
      throw new Error("record switch failed")
    })
    const destroy = vi.fn()
    const mount = vi.fn((_options: PanelMountOptions) => ({
      setRecord,
      destroy,
    }))
    versionedMountImplementation = mount
    const appendSpy = serveVersionedPanelAsset(versionedPanelApi)

    render(<SovereignPanelHost recordId="patient_local_a" />)

    await waitFor(() => expect(destroy).toHaveBeenCalledTimes(1))
    expect(mount).toHaveBeenCalledTimes(1)
    expect(setRecord).toHaveBeenCalledWith("patient_local_a")
    const script = appendSpy.mock.calls[0][0]
    expect(script).toBeInstanceOf(HTMLScriptElement)
    expect((script as HTMLScriptElement).src).toBe(
      "https://provider-sovereign.example/embed/v1/panel.js",
    )
  })

  it("mounts only with a signed-in templated token and rebinds every record", async () => {
    const clerk = signedInClient()
    vi.mocked(getClient).mockResolvedValue(clerk)

    const setRecord = vi.fn(async (_recordId: string) => {})
    const destroy = vi.fn()
    const mount = vi.fn((_options: PanelMountOptions) => ({
      setRecord,
      destroy,
    }))
    expect(window.CosmaNeuraPanel).toBe(versionedPanelApi)
    versionedMountImplementation = mount

    const view = render(<SovereignPanelHost recordId="patient_local_a" />)

    await waitFor(() => expect(mount).toHaveBeenCalledTimes(1))
    expect(clerk.session.getToken).toHaveBeenCalledWith({
      template: "sovereign-provider",
    })
    expect(setRecord).toHaveBeenCalledWith("patient_local_a")

    const options = mount.mock.calls[0][0]
    expect(options.sovereignUrl).toBe("https://provider-sovereign.example")
    expect(options).not.toHaveProperty("onInsert")
    expect(options).not.toHaveProperty("onSave")
    expect(options).not.toHaveProperty("loadHistory")
    expect(options).not.toHaveProperty("saveHistory")
    await expect(options.getContext()).resolves.toEqual({
      clientId: "patient_local_a",
    })
    await expect(options.getAuthToken()).resolves.toBe("provider-token")
    expect(clerk.session.getToken).toHaveBeenLastCalledWith({
      template: "sovereign-provider",
    })

    view.rerender(<SovereignPanelHost recordId="patient_local_b" />)
    await waitFor(() =>
      expect(setRecord).toHaveBeenCalledWith("patient_local_b"),
    )
    await expect(options.getContext()).resolves.toEqual({
      clientId: "patient_local_b",
    })
    expect(mount).toHaveBeenCalledTimes(1)

    view.unmount()
    expect(destroy).toHaveBeenCalledTimes(1)
  })

  it("destroys the rail if a later record switch cannot bind", async () => {
    const clerk = signedInClient()
    vi.mocked(getClient).mockResolvedValue(clerk)

    const setRecord = vi
      .fn<(recordId: string) => Promise<void>>()
      .mockResolvedValueOnce()
      .mockRejectedValueOnce(new Error("record switch failed"))
    const destroy = vi.fn()
    const mount = vi.fn((_options: PanelMountOptions) => ({
      setRecord,
      destroy,
    }))
    versionedMountImplementation = mount

    const view = render(<SovereignPanelHost recordId="patient_local_a" />)
    await waitFor(() =>
      expect(setRecord).toHaveBeenCalledWith("patient_local_a"),
    )

    view.rerender(<SovereignPanelHost recordId="patient_local_b" />)

    await waitFor(() => expect(destroy).toHaveBeenCalledTimes(1))
    expect(setRecord).toHaveBeenCalledWith("patient_local_b")
  })

  it("stays disabled when configuration is absent", async () => {
    vi.stubEnv("VITE_SOVEREIGN_URL", "")
    const clerk = signedInClient()
    vi.mocked(getClient).mockResolvedValue(clerk)
    const mount = vi.fn()
    window.CosmaNeuraPanel = { mount }

    render(<SovereignPanelHost recordId="patient_local_a" />)

    await waitFor(() => expect(getClient).not.toHaveBeenCalled())
    expect(mount).not.toHaveBeenCalled()
  })

  it("fails closed when Clerk cannot mint the configured token", async () => {
    const clerk = signedInClient(null)
    vi.mocked(getClient).mockResolvedValue(clerk)
    const mount = vi.fn()
    window.CosmaNeuraPanel = { mount }

    render(<SovereignPanelHost recordId="patient_local_a" />)

    await waitFor(() => expect(clerk.session.getToken).toHaveBeenCalled())
    expect(mount).not.toHaveBeenCalled()
  })
})
