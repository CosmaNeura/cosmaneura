import { beforeEach, describe, expect, it, vi } from "vitest"

import { clientLoader } from "./onboarding"

const calls = vi.hoisted(() => ({
  settingsData: vi.fn(),
  getPractice: vi.fn(),
  getOrganizationAccessOpportunity: vi.fn(),
  getClient: vi.fn(),
  checkViewAsStatus: vi.fn(),
}))
vi.mock("~/configuration/client", async (original) => ({
  ...(await original<typeof import("~/configuration/client")>()),
  settingsData: calls.settingsData,
}))
vi.mock("~/configuration/clerk", async (original) => ({
  ...(await original<typeof import("~/configuration/clerk")>()),
  getClient: calls.getClient,
}))
vi.mock("~/lib/practice-api", async (original) => ({
  ...(await original<typeof import("~/lib/practice-api")>()),
  getPractice: calls.getPractice,
  getOrganizationAccessOpportunity: calls.getOrganizationAccessOpportunity,
}))
vi.mock("~/lib/view-as-api", async (original) => ({
  ...(await original<typeof import("~/lib/view-as-api")>()),
  checkViewAsStatus: calls.checkViewAsStatus,
}))

beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  calls.settingsData.mockResolvedValue({
    data: { onboarding_completed: true, practice_ready: true },
    error: undefined,
  })
  calls.getPractice.mockResolvedValue(null)
  calls.getOrganizationAccessOpportunity.mockResolvedValue({ state: "ready" })
  calls.getClient.mockResolvedValue({
    user: {
      primaryEmailAddress: { emailAddress: "provider@synthetic.invalid" },
    },
  })
  calls.checkViewAsStatus.mockResolvedValue({
    state: "ready",
    status: { active: false },
  })
})
function load(path: string) {
  return clientLoader({
    request: new Request("https://synthetic.invalid" + path),
  } as Parameters<typeof clientLoader>[0])
}

describe("explicit Scribe setup preserves ordinary onboarding", () => {
  it("retains the notes redirect for a completed grandfathered user on ordinary onboarding", async () => {
    try {
      await load("/onboarding")
      expect.fail("redirect required")
    } catch (response) {
      expect(response).toBeInstanceOf(Response)
      expect((response as Response).status).toBe(302)
      expect((response as Response).headers.get("Location")).toBe("/notes")
    }
  })
  it("keeps explicit setup open for that same completed user without manufacturing a practice", async () => {
    const data = await load("/onboarding?scribe=setup")
    expect(data).toMatchObject({
      scribeSetup: true,
      practice: null,
      onboarding_completed: true,
      practice_ready: true,
    })
  })
  it("does not accept duplicate query choices as the explicit setup mode", async () => {
    await expect(
      load("/onboarding?scribe=setup&scribe=other"),
    ).rejects.toBeInstanceOf(Response)
  })
  it("blocks explicit setup during view-as before any account or practice reads", async () => {
    calls.checkViewAsStatus.mockResolvedValue({
      state: "ready",
      status: { active: true, diagnostic_code: null },
    })
    expect(await load("/onboarding?scribe=setup")).toMatchObject({
      scribeSetupBlocked: true,
    })
    expect(calls.settingsData).not.toHaveBeenCalled()
    expect(calls.getPractice).not.toHaveBeenCalled()
    expect(calls.getOrganizationAccessOpportunity).not.toHaveBeenCalled()
  })
})
