import { beforeEach, describe, expect, it } from "vitest"

import type { BrowserPrincipal } from "./clerk"
import {
  QueryPrincipalChangedError,
  QueryPrincipalUnavailableError,
  bindQueryPrincipal,
  withQueryPrincipal,
} from "./query-principal"
import { queryClient } from "./tanstack-query"

const first: BrowserPrincipal = {
  isLoaded: true,
  userId: "synthetic-a",
  sessionId: "session-a",
}
const second: BrowserPrincipal = {
  isLoaded: true,
  userId: "synthetic-b",
  sessionId: "session-b",
}
const settingsKey = [
  { _id: "settingsData", baseUrl: "https://synthetic.invalid" },
]

beforeEach(() => {
  bindQueryPrincipal({ isLoaded: false, userId: null, sessionId: null })
  queryClient.clear()
})

describe("browser query identity", () => {
  it("clears an unbound cache on the first authenticated identity", () => {
    queryClient.setQueryData(settingsKey, { practice_ready: true })
    bindQueryPrincipal(first)
    expect(queryClient.getQueryData(settingsKey)).toBeUndefined()
  })

  it("preserves same-session consumers but clears settings and notes on an account switch", () => {
    bindQueryPrincipal(first)
    queryClient.setQueryData(settingsKey, { practice_ready: true })
    queryClient.setQueryData(["synthetic-notes"], ["synthetic-note"])
    bindQueryPrincipal(first)
    expect(queryClient.getQueryData(settingsKey)).toEqual({
      practice_ready: true,
    })
    bindQueryPrincipal(second)
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
  })

  it("invalidates a different session of the same user", () => {
    bindQueryPrincipal(first)
    queryClient.setQueryData(settingsKey, true)
    bindQueryPrincipal({ ...first, sessionId: "session-replaced" })
    expect(queryClient.getQueryData(settingsKey)).toBeUndefined()
  })

  it.each([
    { isLoaded: true, userId: null, sessionId: null },
    { isLoaded: false, userId: null, sessionId: null },
  ])(
    "clears cached data when identity becomes unavailable: %j",
    (principal) => {
      bindQueryPrincipal(first)
      queryClient.setQueryData(settingsKey, true)
      bindQueryPrincipal(principal)
      expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
    },
  )

  it("cancels a late old-account query and prevents it from repopulating the cache", async () => {
    bindQueryPrincipal(first)
    let complete!: (value: boolean) => void
    const pending = queryClient
      .fetchQuery({
        queryKey: settingsKey,
        queryFn: () =>
          new Promise<boolean>((resolve) => {
            complete = resolve
          }),
      })
      .catch(() => "cancelled")
    bindQueryPrincipal(second)
    complete(true)
    expect(await pending).toBe("cancelled")
    expect(queryClient.getQueryData(settingsKey)).toBeUndefined()
  })

  it("rejects a successful access response after an account switch", async () => {
    let principal = first
    await expect(
      withQueryPrincipal(
        async () => principal,
        async () => {
          principal = second
          return { practice_ready: true }
        },
      ),
    ).rejects.toBeInstanceOf(QueryPrincipalChangedError)
  })

  it("rejects a stale redirect/error after an account switch", async () => {
    let principal = first
    await expect(
      withQueryPrincipal(
        async () => principal,
        async () => {
          principal = second
          throw new Error("synthetic old-account redirect")
        },
      ),
    ).rejects.toBeInstanceOf(QueryPrincipalChangedError)
  })

  it("detects a switch away and back during a pending read", async () => {
    await expect(
      withQueryPrincipal(
        async () => first,
        async () => {
          bindQueryPrincipal(second)
          bindQueryPrincipal(first)
          return true
        },
      ),
    ).rejects.toBeInstanceOf(QueryPrincipalChangedError)
  })

  it("refuses an unauthenticated read before requesting account data", async () => {
    let reads = 0
    await expect(
      withQueryPrincipal(
        async () => ({ isLoaded: true, userId: null, sessionId: null }),
        async () => {
          reads += 1
          return true
        },
      ),
    ).rejects.toBeInstanceOf(QueryPrincipalUnavailableError)
    expect(reads).toBe(0)
  })
})
