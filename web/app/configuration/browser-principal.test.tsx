import { afterEach, describe, expect, it, vi } from "vitest"

import { useBrowserPrincipal } from "./clerk"
import { act, cleanup, renderHook } from "@testing-library/react"

vi.hoisted(() => vi.stubEnv("VITE_CLERK_PUBLIC_KEY", "pk_test_synthetic"))
const auth = vi.hoisted(() => ({
  loaded: true,
  user: { id: "synthetic_user_a" } as { id: string } | null,
  session: { id: "synthetic_session_a" } as { id: string } | null,
  listeners: new Set<(resources: unknown) => void>(),
}))
vi.mock("@clerk/clerk-react", () => ({
  ClerkProvider: () => null,
  useAuth: () => ({
    isLoaded: auth.loaded,
    userId: auth.user?.id,
    sessionId: auth.session?.id,
  }),
  useClerk: () => live,
}))
const live = {
  get loaded() {
    return auth.loaded
  },
  get user() {
    return auth.user
  },
  get session() {
    return auth.session
  },
  addListener(callback: (resources: unknown) => void) {
    auth.listeners.add(callback)
    return () => {
      auth.listeners.delete(callback)
    }
  },
}
const sdk = {
  loaded: true,
  user: { id: "synthetic_user_a" },
  session: { id: "synthetic_session_a", getToken: vi.fn() },
  load: vi.fn(),
  redirectToSignIn: vi.fn(),
}
Object.assign(window, { Clerk: sdk })
afterEach(() => {
  cleanup()
  auth.loaded = true
  auth.user = { id: "synthetic_user_a" }
  auth.session = { id: "synthetic_session_a" }
  auth.listeners.clear()
  sdk.user.id = "synthetic_user_a"
  sdk.session.id = "synthetic_session_a"
})
describe("normalized browser principal at the actual vendor adapter", () => {
  it("observes actual SDK replacement after an await while React resources lag", async () => {
    const { result } = renderHook(useBrowserPrincipal)
    const read = await result.current.identity.readCurrent()
    expect(read().sessionId).toBe("synthetic_session_a")
    sdk.user.id = "synthetic_user_b"
    sdk.session.id = "synthetic_session_b"
    await Promise.resolve()
    expect(read()).toEqual({
      isLoaded: true,
      userId: "synthetic_user_b",
      sessionId: "synthetic_session_b",
    })
    expect(result.current.identity.current().userId).toBe("synthetic_user_a")
    expect(sdk.session.getToken).not.toHaveBeenCalled()
    expect(sdk.load).not.toHaveBeenCalled()
  })
  it("normalizes signout and pending loading without inventing a session", () => {
    const { result, rerender } = renderHook(useBrowserPrincipal)
    auth.session = null
    auth.loaded = false
    rerender()
    expect(result.current).toMatchObject({ isLoaded: false, sessionId: null })
    auth.user = null
    expect(result.current.identity.current().userId).toBeNull()
  })
  it("normalizes real listener resources and removes the listener", () => {
    const { result } = renderHook(useBrowserPrincipal)
    const listener = vi.fn()
    const unsubscribe = result.current.identity.subscribe(listener)
    auth.user = { id: "synthetic_user_b" }
    auth.session = { id: "synthetic_session_b" }
    act(() =>
      auth.listeners.forEach((callback) =>
        callback({ user: auth.user, session: auth.session }),
      ),
    )
    expect(listener).toHaveBeenCalledWith({
      isLoaded: true,
      userId: "synthetic_user_b",
      sessionId: "synthetic_session_b",
    })
    unsubscribe()
    expect(auth.listeners.size).toBe(0)
  })
})
