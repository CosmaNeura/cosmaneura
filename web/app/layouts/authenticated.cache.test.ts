import { beforeEach, describe, expect, it, vi } from "vitest"

import { bindQueryPrincipal } from "~/configuration/query-principal"
import { queryClient } from "~/configuration/tanstack-query"

import { clientLoader } from "./authenticated"

const { readPrincipal, readSettings, key } = vi.hoisted(() => ({
  readPrincipal: vi.fn(),
  readSettings: vi.fn(),
  key: [{ _id: "settingsData", baseUrl: "https://synthetic.invalid" }],
}))
vi.mock("~/configuration/clerk", () => ({
  readCurrentBrowserPrincipal: readPrincipal,
  getClient: vi.fn(),
}))
vi.mock("~/lib/view-as-api", () => ({
  checkViewAsStatus: async () => ({
    state: "ready",
    status: { active: false },
  }),
  getViewAsStatus: vi.fn(),
}))
vi.mock("client/@tanstack/react-query.gen", () => ({
  settingsDataOptions: () => ({ queryKey: key, queryFn: readSettings }),
}))

beforeEach(() => {
  bindQueryPrincipal({ isLoaded: false, userId: null, sessionId: null })
  queryClient.clear()
  readPrincipal
    .mockReset()
    .mockResolvedValue({
      isLoaded: true,
      userId: "synthetic-a",
      sessionId: "session-a",
    })
  readSettings.mockReset()
})
const load = () =>
  clientLoader({
    request: new Request("https://synthetic.invalid/notes"),
  } as Parameters<typeof clientLoader>[0])

describe("authenticated settings cache", () => {
  it("rechecks server access instead of reusing a completed account's cached settings", async () => {
    bindQueryPrincipal({
      isLoaded: true,
      userId: "synthetic-a",
      sessionId: "session-a",
    })
    queryClient.setQueryData(key, {
      onboarding_completed: true,
      practice_ready: true,
    })
    readSettings.mockResolvedValue({
      onboarding_completed: false,
      practice_ready: false,
    })
    const redirect = await load().catch((error: unknown) => error)
    expect(readSettings).toHaveBeenCalledOnce()
    expect(redirect).toBeInstanceOf(Response)
    expect((redirect as Response).headers.get("Location")).toBe("/onboarding")
  })

  it("discards a pending old-account response and routes through fresh onboarding", async () => {
    readSettings.mockImplementation(async () => {
      readPrincipal.mockResolvedValue({
        isLoaded: true,
        userId: "synthetic-b",
        sessionId: "session-b",
      })
      return { onboarding_completed: true, practice_ready: true }
    })
    const redirect = await load().catch((error: unknown) => error)
    expect(redirect).toBeInstanceOf(Response)
    expect((redirect as Response).headers.get("Location")).toBe("/onboarding")
    expect(queryClient.getQueryData(key)).toBeUndefined()
  })
})
