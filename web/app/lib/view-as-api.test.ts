import { describe, expect, it, vi } from "vitest"

import {
  type ViewAsStatus,
  checkAuthenticatedViewAsStatus,
  checkViewAsStatus,
} from "./view-as-api"
import { ViewAsControlRequestError } from "./view-as-binding"

const inactiveStatus: ViewAsStatus = {
  request_binding: "inactive",
  can_view_as: false,
  active: false,
  read_only: false,
  diagnostic_code: null,
  expires_at: null,
  actor: null,
  target: null,
}

describe("checkViewAsStatus", () => {
  it("keeps a successful inactive check distinct from an unavailable check", async () => {
    await expect(
      checkViewAsStatus(vi.fn().mockResolvedValue(inactiveStatus)),
    ).resolves.toEqual({ state: "ready", status: inactiveStatus })
  })

  it("returns a fail-closed state when status cannot be verified", async () => {
    await expect(
      checkViewAsStatus(vi.fn().mockRejectedValue(new Error("offline"))),
    ).resolves.toEqual({ state: "unavailable", status: null })
  })

  it("does not call the control plane for a signed-out public visitor", async () => {
    const readStatus = vi.fn()

    await expect(
      checkAuthenticatedViewAsStatus(
        vi.fn().mockResolvedValue(null),
        readStatus,
      ),
    ).resolves.toEqual({ state: "signed_out", status: null })
    expect(readStatus).not.toHaveBeenCalled()
  })

  it("checks view-as for an authenticated visitor on a public route", async () => {
    const readStatus = vi.fn().mockResolvedValue(inactiveStatus)

    await expect(
      checkAuthenticatedViewAsStatus(
        vi.fn().mockResolvedValue("current-session-token"),
        readStatus,
      ),
    ).resolves.toEqual({ state: "ready", status: inactiveStatus })
    expect(readStatus).toHaveBeenCalledWith("current-session-token")
  })

  it("keeps a backend 401 fail closed when Clerk still returned a token", async () => {
    await expect(
      checkAuthenticatedViewAsStatus(
        vi.fn().mockResolvedValue("expired-session-token"),
        vi.fn().mockRejectedValue(new ViewAsControlRequestError(401)),
      ),
    ).resolves.toEqual({ state: "unavailable", status: null })
  })
})
