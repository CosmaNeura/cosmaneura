import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const fetch = vi.hoisted(() => vi.fn())
const binding = vi.hoisted(() => vi.fn())
const enforce = vi.hoisted(() => vi.fn())
vi.mock("~/lib/view-as-binding", () => ({
  bindInternalApiRequest: binding,
  enforceViewAsBindingResponse: enforce,
  isViewAsControlRequest: () => false,
}))
vi.mock("@clerk/clerk-react", () => ({
  ClerkProvider: () => null,
  useAuth: vi.fn(),
  useClerk: vi.fn(),
}))
vi.mock("@clerk/shared/loadClerkJsScript", () => ({
  loadClerkJsScript: vi.fn(),
}))
const expected = {
  isLoaded: true,
  userId: "synthetic_user_a",
  sessionId: "synthetic_session_a",
}
const body = {
  practice_ref: "practice_00000000000000000000000001",
  baa_reference: "Synthetic BAA",
  attestation_name: "Synthetic Clinician",
  checks: ["workforce", "not-party", "direction", "phi"],
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
function sdk() {
  return {
    loaded: true,
    user: { id: expected.userId },
    session: {
      id: expected.sessionId,
      getToken: vi.fn().mockResolvedValue("synthetic-token-a"),
    },
    load: vi.fn(),
    redirectToSignIn: vi.fn(),
  }
}
beforeEach(() => {
  vi.resetModules()
  fetch.mockReset()
  binding.mockReset().mockImplementation(async (_input, init) => init)
  enforce.mockReset().mockResolvedValue(undefined)
  vi.stubGlobal("fetch", fetch)
  vi.stubEnv("VITE_CLERK_PUBLIC_KEY", "pk_test_synthetic")
  vi.stubEnv("VITE_PYTHON_URL", "https://synthetic.invalid")
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
describe("own-member attestation dispatch identity", () => {
  it("does not dispatch A's intent as B after SDK loading yields", async () => {
    const client = sdk()
    const loading = deferred<void>()
    client.loaded = false
    client.load.mockImplementation(() => loading.promise)
    vi.stubGlobal("Clerk", client)
    const { attestCurrentPracticeMember } = await import("./practice-api")
    const pending = attestCurrentPracticeMember(body, {
      principal: expected,
      isCurrent: () => true,
    })
    const refused = expect(pending).rejects.toThrow(/account|session/i)
    await vi.waitFor(() => expect(client.load).toHaveBeenCalledTimes(1))
    client.loaded = true
    client.user = { id: "synthetic_user_b" }
    client.session = {
      id: "synthetic_session_b",
      getToken: vi.fn().mockResolvedValue("synthetic-token-b"),
    }
    loading.resolve()
    await refused
    expect(client.session.getToken).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each(["account", "session", "same-id-resource", "context"])(
    "blocks dispatch when %s changes during token retrieval",
    async (change) => {
      const client = sdk()
      const token = deferred<string>()
      client.session.getToken.mockReturnValue(token.promise)
      vi.stubGlobal("Clerk", client)
      let current = true
      const { attestCurrentPracticeMember } = await import("./practice-api")
      const pending = attestCurrentPracticeMember(body, {
        principal: expected,
        isCurrent: () => current,
      })
      const refused = expect(pending).rejects.toThrow(/account|session/i)
      await vi.waitFor(() =>
        expect(client.session.getToken).toHaveBeenCalledTimes(1),
      )
      if (change === "account") client.user = { id: "synthetic_user_b" }
      else if (change === "session") client.session.id = "synthetic_session_b"
      else if (change === "same-id-resource")
        client.session = { id: expected.sessionId, getToken: vi.fn() }
      else current = false
      token.resolve("synthetic-token-a")
      await refused
      expect(fetch).not.toHaveBeenCalled()
    },
  )

  it("sends one same-session request with the captured token", async () => {
    const client = sdk()
    vi.stubGlobal("Clerk", client)
    fetch.mockResolvedValue(new Response(JSON.stringify({ attested: true })))
    const { attestCurrentPracticeMember } = await import("./practice-api")
    await expect(
      attestCurrentPracticeMember(body, {
        principal: expected,
        isCurrent: () => true,
      }),
    ).resolves.toEqual({ attested: true })
    expect(client.session.getToken).toHaveBeenCalledTimes(1)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch).toHaveBeenCalledWith(
      "https://synthetic.invalid/internal/v1/practice/attestation",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(body),
        headers: {
          Authorization: "Bearer synthetic-token-a",
          "Content-Type": "application/json",
        },
      }),
    )
  })

  it("rejects a late response after the token's session changed", async () => {
    const client = sdk()
    vi.stubGlobal("Clerk", client)
    const response = deferred<Response>()
    fetch.mockReturnValue(response.promise)
    const { attestCurrentPracticeMember } = await import("./practice-api")
    const pending = attestCurrentPracticeMember(body, {
      principal: expected,
      isCurrent: () => true,
    })
    const refused = expect(pending).rejects.toThrow(/account|session/i)
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    client.session.id = "synthetic_session_b"
    response.resolve(new Response(JSON.stringify({ attested: true })))
    await refused
  })

  it("checks the session at actual dispatch after view-as binding yields", async () => {
    const client = sdk()
    vi.stubGlobal("Clerk", client)
    const bound = deferred<RequestInit>()
    binding.mockReturnValue(bound.promise)
    fetch.mockResolvedValue(new Response(JSON.stringify({ attested: true })))
    const { attestCurrentPracticeMember } = await import("./practice-api")
    const pending = attestCurrentPracticeMember(body, {
      principal: expected,
      isCurrent: () => true,
    })
    const refused = expect(pending).rejects.toThrow(/account|session/i)
    await vi.waitFor(() => expect(binding).toHaveBeenCalledTimes(1))
    client.session.id = "synthetic_session_b"
    bound.resolve(binding.mock.calls[0][1])
    await refused
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each(["network", "response-binding"])(
    "cannot navigate a new account from a stale 402 after %s yields",
    async (phase) => {
      const client = sdk()
      vi.stubGlobal("Clerk", client)
      const response = deferred<Response>()
      const checked = deferred<void>()
      fetch.mockReturnValue(response.promise)
      if (phase === "response-binding") enforce.mockReturnValue(checked.promise)
      const navigate = vi
        .spyOn(window.location, "replace")
        .mockImplementation(() => {})
      const { attestCurrentPracticeMember } = await import("./practice-api")
      const pending = attestCurrentPracticeMember(body, {
        principal: expected,
        isCurrent: () => true,
      })
      const refused = expect(pending).rejects.toThrow(/account|session/i)
      await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
      if (phase === "network") client.session.id = "synthetic_session_b"
      response.resolve(new Response(null, { status: 402 }))
      if (phase === "response-binding") {
        await vi.waitFor(() => expect(enforce).toHaveBeenCalledTimes(1))
        client.session.id = "synthetic_session_b"
        checked.resolve()
      }
      await refused
      expect(navigate).not.toHaveBeenCalled()
    },
  )
})
