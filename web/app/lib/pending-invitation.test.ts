import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  PENDING_INVITATION_KEY,
  capturePendingInvitation,
  clearPendingInvitation,
  pendingInvitation,
} from "./pending-invitation"

beforeEach(() => {
  clearPendingInvitation()
  window.sessionStorage.clear()
  window.history.replaceState(null, "", "/")
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("pending invitation without session storage", () => {
  it("keeps the token for this page load when storage refuses writes", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError")
    })
    window.history.replaceState(null, "", "/invite#t=quota-token")

    expect(capturePendingInvitation()).toBe("quota-token")
    expect(window.location.hash).toBe("")
    expect(pendingInvitation()).toBe("quota-token")

    clearPendingInvitation()
    expect(pendingInvitation()).toBeNull()
  })

  it("keeps the token for this page load when storage is blocked", () => {
    vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError")
    })
    window.history.replaceState(null, "", "/invite#t=blocked-token")

    expect(capturePendingInvitation()).toBe("blocked-token")
    expect(window.location.hash).toBe("")
    expect(pendingInvitation()).toBe("blocked-token")
  })

  it("prefers what storage holds when it works", () => {
    window.history.replaceState(null, "", "/invite#t=stored-token")

    expect(capturePendingInvitation()).toBe("stored-token")
    expect(window.sessionStorage.getItem(PENDING_INVITATION_KEY)).toBe(
      "stored-token",
    )
  })
})
