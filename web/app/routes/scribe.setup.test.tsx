import { beforeEach, describe, expect, it, vi } from "vitest"

import { clientLoader } from "./scribe.setup"

const calls = vi.hoisted(() => ({
  getClient: vi.fn(),
  checkViewAsStatus: vi.fn(),
  getPractice: vi.fn(),
}))
vi.mock("~/configuration/clerk", () => ({ getClient: calls.getClient }))
vi.mock("~/lib/view-as-api", () => ({
  checkViewAsStatus: calls.checkViewAsStatus,
}))
vi.mock("~/lib/practice-api", () => ({ getPractice: calls.getPractice }))

beforeEach(() => {
  vi.resetAllMocks()
  calls.getClient.mockResolvedValue({ session: {} })
  calls.checkViewAsStatus.mockResolvedValue({
    state: "ready",
    status: { active: false },
  })
  calls.getPractice.mockResolvedValue(null)
})

describe("authenticated Scribe setup reads", () => {
  it("does not read a practice while signed out", async () => {
    calls.getClient.mockResolvedValue(null)
    expect(await clientLoader()).toEqual({ kind: "signing-in" })
    expect(calls.checkViewAsStatus).not.toHaveBeenCalled()
    expect(calls.getPractice).not.toHaveBeenCalled()
  })
  it.each([
    { state: "unavailable" },
    { state: "ready", status: { active: true } },
  ])("refuses practice reads with unknown or active view-as", async (check) => {
    calls.checkViewAsStatus.mockResolvedValue(check)
    expect((await clientLoader()).kind).toBe("blocked")
    expect(calls.getPractice).not.toHaveBeenCalled()
  })
  it("reads only the existing caller-bound practice metadata", async () => {
    const result = await clientLoader()
    expect(result.kind).toBe("ready")
    expect(calls.getPractice).toHaveBeenCalledExactlyOnceWith()
  })
  it("does not expose private server error details or manufacture enrollment on a failed read", async () => {
    calls.getPractice.mockRejectedValue(new Error("synthetic-private-detail"))
    const result = await clientLoader()
    expect(result.kind).toBe("unavailable")
    expect(JSON.stringify(result)).not.toContain("synthetic-private-detail")
    expect(result).not.toHaveProperty("practice")
  })
})
