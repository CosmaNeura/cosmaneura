import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

type TestClerkClient = {
  loaded: boolean
  load: () => Promise<void>
  user?: { id: string }
  session?: {
    getToken: (options?: { template?: string }) => Promise<string | null>
  }
  redirectToSignIn: () => Promise<void>
}

function installClerk(client: TestClerkClient) {
  Object.defineProperty(window, "Clerk", {
    configurable: true,
    writable: true,
    value: client,
  })
}

describe("authenticated Clerk boundary", () => {
  beforeEach(() => {
    vi.resetModules()
    window.sessionStorage.clear()
    window.history.replaceState({}, "", "/")
  })

  afterEach(() => {
    vi.restoreAllMocks()
    Reflect.deleteProperty(window, "Clerk")
  })

  it("redirects a signed-out visitor locally and preserves the fragment token in session storage", async () => {
    const invitationToken = "a".repeat(43)
    window.sessionStorage.setItem(
      "cosmas.public-practice-invitation.v1",
      invitationToken,
    )
    window.history.replaceState(
      {},
      "",
      "/onboarding?channel=direct#registrationToken=not-forwarded",
    )
    installClerk({
      loaded: true,
      load: async () => {},
      redirectToSignIn: async () => {},
    })
    const replace = vi
      .spyOn(window.location, "replace")
      .mockImplementation(() => {})
    const { getClient, localSignInRedirectUrl, SignInRedirectInProgressError } =
      await import("./clerk")

    const client = getClient()

    await expect(client).rejects.toBeInstanceOf(SignInRedirectInProgressError)
    await expect(client).rejects.toMatchObject({
      redirectUrl: "/sign-in?redirect_url=%2Fonboarding%3Fchannel%3Ddirect",
    })
    expect(localSignInRedirectUrl()).toBe(
      "/sign-in?redirect_url=%2Fonboarding%3Fchannel%3Ddirect",
    )
    expect(replace).toHaveBeenCalledWith(
      "/sign-in?redirect_url=%2Fonboarding%3Fchannel%3Ddirect",
    )
    expect(replace.mock.calls[0][0]).not.toContain("registrationToken")
    expect(
      window.sessionStorage.getItem("cosmas.public-practice-invitation.v1"),
    ).toBe(invitationToken)
  })

  it("redirects when an existing Clerk session can no longer issue a token", async () => {
    window.history.replaceState(
      {},
      "",
      "/schedule?day=2026-08-27#private-fragment",
    )
    const getToken = vi.fn(async () => null)
    installClerk({
      loaded: true,
      load: async () => {},
      user: { id: "user_expired" },
      session: { getToken },
      redirectToSignIn: async () => {},
    })
    const replace = vi
      .spyOn(window.location, "replace")
      .mockImplementation(() => {})
    const { getSessionToken, SignInRedirectInProgressError } = await import(
      "./clerk"
    )

    await expect(getSessionToken()).rejects.toBeInstanceOf(
      SignInRedirectInProgressError,
    )
    expect(getToken).toHaveBeenCalledOnce()
    expect(replace).toHaveBeenCalledWith(
      "/sign-in?redirect_url=%2Fschedule%3Fday%3D2026-08-27",
    )
    expect(replace.mock.calls[0][0]).not.toContain("private-fragment")
  })

  it.each(["/sign-in?redirect_url=%2Fnotes", "/sign-up?channel=direct"])(
    "treats a null token on %s as signed out without a redirect loop",
    async (path) => {
      window.history.replaceState({}, "", `${path}#private-fragment`)
      const getToken = vi.fn(async () => null)
      installClerk({
        loaded: true,
        load: async () => {},
        user: { id: "user_expired" },
        session: { getToken },
        redirectToSignIn: async () => {},
      })
      const replace = vi
        .spyOn(window.location, "replace")
        .mockImplementation(() => {})
      const {
        getOptionalSessionToken,
        getSessionToken,
        SignedOutSessionError,
      } = await import("./clerk")

      await expect(getOptionalSessionToken()).resolves.toBeNull()
      await expect(getSessionToken()).rejects.toBeInstanceOf(
        SignedOutSessionError,
      )
      expect(replace).not.toHaveBeenCalled()
    },
  )
})
