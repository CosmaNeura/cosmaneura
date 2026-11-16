import type { BrowserPrincipal } from "./clerk"
import { queryClient } from "./tanstack-query"

let current: BrowserPrincipal | null = null
let generation = 0

/** Ephemeral SDK identity only; never persist it or attach it to records. */
export function bindQueryPrincipal(principal: BrowserPrincipal) {
  const next = principal.isLoaded ? { ...principal } : null
  if (
    current?.isLoaded !== next?.isLoaded ||
    current?.userId !== next?.userId ||
    current?.sessionId !== next?.sessionId
  ) {
    // Destroy cached queries and cancel their in-flight reads together. An old
    // account's pending response must not repopulate the shared cache.
    queryClient.clear()
    current = next
    generation += 1
  }
  return generation
}

export class QueryPrincipalChangedError extends Error {
  constructor() {
    super("The signed-in account changed while checking access.")
    this.name = "QueryPrincipalChangedError"
  }
}

export class QueryPrincipalUnavailableError extends Error {
  constructor() {
    super("A signed-in account is required to check access.")
    this.name = "QueryPrincipalUnavailableError"
  }
}

/** Fence both successful reads and redirects/errors against a changed actor. */
export async function withQueryPrincipal<T>(
  readPrincipal: () => Promise<BrowserPrincipal>,
  read: () => Promise<T>,
): Promise<T> {
  const before = await readPrincipal()
  const expected = bindQueryPrincipal(before)
  if (!before.isLoaded || !before.userId || !before.sessionId) {
    throw new QueryPrincipalUnavailableError()
  }
  const checkCurrent = async () => {
    if (bindQueryPrincipal(await readPrincipal()) !== expected) {
      throw new QueryPrincipalChangedError()
    }
  }
  let result: T
  try {
    result = await read()
  } catch (error) {
    await checkCurrent()
    throw error
  }
  await checkCurrent()
  return result
}
