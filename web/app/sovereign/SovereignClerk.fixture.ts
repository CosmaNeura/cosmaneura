import type { BrowserPrincipal } from "~/configuration/clerk"

// Source-only declared Clerk resources, never an authority or token fallback.
type Client =
  | {
      user?: { id: string } | null
      session?: { id?: string; getToken?: unknown } | null
      loaded?: boolean
    }
  | null
  | undefined
let client: Client = null
let readClient: () => Promise<unknown> = async () => null
const listeners = new Set<(principal: BrowserPrincipal) => void>()
const normalize = (value: Client): BrowserPrincipal => ({
  isLoaded: value?.loaded !== false,
  userId: value?.user?.id ?? null,
  sessionId: value?.session?.id ?? null,
})
const identity = {
  current: () => normalize(client),
  subscribe(callback: (principal: BrowserPrincipal) => void) {
    listeners.add(callback)
    return () => {
      listeners.delete(callback)
    }
  },
  readCurrent: async () => {
    const resource = await readClient()
    return () => normalize(resource as Client)
  },
}
export const syntheticClerk = {
  bind(value: Client, notify = false) {
    client = value
    if (notify) listeners.forEach((callback) => callback(normalize(client)))
  },
  bindResource<T>(value: T): T {
    this.bind(value as unknown as Client)
    return value
  },
  principal(read: () => Promise<unknown>) {
    readClient = read
    return { ...identity.current(), identity }
  },
}
