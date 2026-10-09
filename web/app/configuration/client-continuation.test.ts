import { beforeEach, expect, it, vi } from "vitest"

import { billingContinuationAuth } from "./client"

const state = vi.hoisted(() => ({
  getToken: vi.fn(),
  getClient: vi.fn(),
  setConfig: vi.fn(),
}))
vi.mock("./clerk", () => ({ getClient: state.getClient }))
vi.mock("client/client.gen", () => ({ client: { setConfig: state.setConfig } }))
vi.mock("~/utils/environment", () => ({
  isDevelopment: () => false,
  requireEnv: () => "/",
}))
vi.mock("~/lib/billing-recovery", () => ({ internalApiFetch: vi.fn() }))
vi.mock("@sentry/react", () => ({ setUser: vi.fn() }))

beforeEach(() => {
  state.getToken.mockReset()
  state.getClient.mockReset()
  state.getClient.mockResolvedValue({
    session: { getToken: state.getToken },
    user: { id: "synthetic" },
  })
  state.getToken.mockResolvedValue("synthetic-opaque-token")
})
it("requests fresh ordinary same-session evidence only for explicit continuation", async () => {
  await billingContinuationAuth()
  expect(state.getToken).toHaveBeenLastCalledWith({ skipCache: true })
  const configured = state.setConfig.mock.calls[0][0]
  await configured.auth()
  expect(state.getToken).toHaveBeenLastCalledWith()
})
it("cannot continue when the current provider session has disappeared", async () => {
  state.getClient.mockResolvedValue({ user: { id: "synthetic" } })
  await expect(billingContinuationAuth()).rejects.toThrow()
  expect(state.getToken).not.toHaveBeenCalled()
})
