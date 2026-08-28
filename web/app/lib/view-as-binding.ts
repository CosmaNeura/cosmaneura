import { requireEnv } from "~/utils/environment"

export const VIEW_AS_REQUEST_BINDING_HEADER =
  "X-Cosmaneura-View-As-Binding" as const
export const VIEW_AS_BINDING_STALE_CODE = "VIEW_AS_BINDING_STALE" as const
export const VIEW_AS_HARD_BLOCK_EVENT = "cosmaneura:view-as-hard-block" as const

const VIEW_AS_BINDING_STORAGE_KEY = "cosmaneura:view-as-request-binding:v1"
const VIEW_AS_STATE_CHANNEL = "cosmaneura:view-as-state:v1"
const VIEW_AS_STATE_STORAGE_SIGNAL = "cosmaneura:view-as-state-changed:v1"
const ACTIVE_BINDING_PATTERN = /^vab1\.[0-9a-f]{64}$/
const VIEW_AS_CONTROL_PATHS = new Set([
  "/internal/v1/view-as/status",
  "/internal/v1/view-as/targets",
  "/internal/v1/view-as/sessions",
  "/internal/v1/view-as/sessions/current",
])

export type ViewAsRequestBinding = "inactive" | `vab1.${string}`

type BindingStatus = {
  request_binding: ViewAsRequestBinding
}

let expectedBinding: ViewAsRequestBinding | null | undefined
let bootstrapPromise: Promise<ViewAsRequestBinding> | null = null
let hardBlocked = false
let reloadRequested = false

export class ViewAsBindingStaleError extends Error {
  constructor() {
    super("The support-view state changed. Reloading before continuing.")
    this.name = "ViewAsBindingStaleError"
  }
}

export class ViewAsControlRequestError extends Error {
  readonly status: number

  constructor(status: number) {
    super("View-as is unavailable")
    this.name = "ViewAsControlRequestError"
    this.status = status
  }
}

export function isViewAsRequestBinding(
  value: unknown,
): value is ViewAsRequestBinding {
  return (
    value === "inactive" ||
    (typeof value === "string" && ACTIVE_BINDING_PATTERN.test(value))
  )
}

function apiBase() {
  return requireEnv("VITE_PYTHON_URL").replace(/\/$/, "")
}

async function authHeaders(sessionToken?: string): Promise<HeadersInit> {
  let token = sessionToken
  if (!token) {
    const { getSessionToken } = await import("~/configuration/clerk")
    token = await getSessionToken()
  }
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  }
}

function sessionStore() {
  if (typeof window === "undefined") return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function clearStoredBinding() {
  try {
    sessionStore()?.removeItem(VIEW_AS_BINDING_STORAGE_KEY)
  } catch {
    // In-memory fail-closed state still protects the current document.
  }
}

function readExpectedBinding(): ViewAsRequestBinding | null {
  if (expectedBinding !== undefined) return expectedBinding
  let stored: string | null = null
  try {
    stored = sessionStore()?.getItem(VIEW_AS_BINDING_STORAGE_KEY) ?? null
  } catch {
    // Bootstrap from the server when storage is unavailable.
  }
  if (stored === null) {
    expectedBinding = null
    return null
  }
  if (!isViewAsRequestBinding(stored)) {
    clearStoredBinding()
    expectedBinding = null
    return null
  }
  expectedBinding = stored
  return stored
}

function storeExpectedBinding(binding: ViewAsRequestBinding) {
  expectedBinding = binding
  try {
    sessionStore()?.setItem(VIEW_AS_BINDING_STORAGE_KEY, binding)
  } catch {
    // The in-memory expectation remains authoritative for this document.
  }
}

function defaultReload() {
  window.location.reload()
}

export function hardBlockViewAsBinding(
  reload: () => void = defaultReload,
): ViewAsBindingStaleError {
  hardBlocked = true
  expectedBinding = null
  bootstrapPromise = null
  clearStoredBinding()
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(VIEW_AS_HARD_BLOCK_EVENT))
    if (!reloadRequested) {
      reloadRequested = true
      try {
        reload()
      } catch {
        // Rendering remains blocked even if the browser refuses navigation.
      }
    }
  }
  return new ViewAsBindingStaleError()
}

export function observeViewAsStatusBinding<T extends BindingStatus>(
  status: T,
): T {
  if (!isViewAsRequestBinding(status.request_binding)) {
    throw hardBlockViewAsBinding()
  }
  if (hardBlocked) throw new ViewAsBindingStaleError()

  const expected = readExpectedBinding()
  if (expected === null) {
    storeExpectedBinding(status.request_binding)
    return status
  }
  if (expected !== status.request_binding) {
    throw hardBlockViewAsBinding()
  }
  return status
}

export function commitViewAsControlTransition<T extends BindingStatus>(
  status: T,
): T {
  if (!isViewAsRequestBinding(status.request_binding)) {
    throw hardBlockViewAsBinding()
  }
  if (hardBlocked) throw new ViewAsBindingStaleError()
  storeExpectedBinding(status.request_binding)
  publishViewAsStateChanged()
  return status
}

export async function rawViewAsControlRequest<T>(
  path: string,
  init?: RequestInit,
  auth?: { sessionToken: string; redirectOnUnauthorized?: boolean },
): Promise<T> {
  const headers = new Headers(await authHeaders(auth?.sessionToken))
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
  const response = await globalThis.fetch(
    `${apiBase()}/internal/v1/view-as${path}`,
    {
      ...init,
      credentials: "include",
      cache: "no-store",
      headers,
    },
  )
  if (response.status === 401 && auth?.redirectOnUnauthorized !== false) {
    const { redirectToLocalSignIn } = await import("~/configuration/clerk")
    redirectToLocalSignIn()
  }
  if (!response.ok) throw new ViewAsControlRequestError(response.status)
  return (await response.json()) as T
}

export async function ensureViewAsRequestBinding(
  bootstrap: () => Promise<BindingStatus> = () =>
    rawViewAsControlRequest<BindingStatus>("/status"),
): Promise<ViewAsRequestBinding> {
  if (hardBlocked) throw new ViewAsBindingStaleError()
  const expected = readExpectedBinding()
  if (expected !== null) return expected

  if (bootstrapPromise === null) {
    bootstrapPromise = bootstrap()
      .then((status) => observeViewAsStatusBinding(status).request_binding)
      .finally(() => {
        bootstrapPromise = null
      })
  }
  return bootstrapPromise
}

export async function bindInternalApiRequest(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<RequestInit> {
  const binding = await ensureViewAsRequestBinding()
  if (hardBlocked) throw new ViewAsBindingStaleError()

  const headers = new Headers(
    input instanceof Request ? input.headers : undefined,
  )
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
  headers.set(VIEW_AS_REQUEST_BINDING_HEADER, binding)
  return { ...init, headers }
}

export function isViewAsControlRequest(
  input: RequestInfo | URL,
  currentUrl: string,
) {
  try {
    const rawUrl = input instanceof Request ? input.url : input.toString()
    const pathname = new URL(rawUrl, currentUrl).pathname.replace(/\/+$/, "")
    return VIEW_AS_CONTROL_PATHS.has(pathname)
  } catch {
    return false
  }
}

export async function enforceViewAsBindingResponse(
  response: Response,
  reload: () => void = defaultReload,
) {
  if (response.status !== 409) return response
  let code: unknown
  try {
    const body = (await response.clone().json()) as {
      detail?: { code?: unknown }
    }
    code = body.detail?.code
  } catch {
    return response
  }
  if (code !== VIEW_AS_BINDING_STALE_CODE) return response
  throw hardBlockViewAsBinding(reload)
}

export function publishViewAsStateChanged() {
  if (typeof window === "undefined") return
  if (typeof BroadcastChannel !== "undefined") {
    let channel: BroadcastChannel | null = null
    try {
      channel = new BroadcastChannel(VIEW_AS_STATE_CHANNEL)
      channel.postMessage({ type: "state-changed" })
      channel.close()
      return
    } catch {
      try {
        channel?.close()
      } catch {
        // Fall through to the storage signal.
      }
    }
  }
  try {
    window.localStorage.setItem(VIEW_AS_STATE_STORAGE_SIGNAL, "state-changed")
    window.localStorage.removeItem(VIEW_AS_STATE_STORAGE_SIGNAL)
  } catch {
    // Focus/visibility/periodic revalidation remains as the fallback.
  }
}

export function subscribeToViewAsStateChanges(callback: () => void) {
  if (typeof window === "undefined") return () => undefined
  if (typeof BroadcastChannel !== "undefined") {
    let channel: BroadcastChannel | null = null
    try {
      channel = new BroadcastChannel(VIEW_AS_STATE_CHANNEL)
      channel.addEventListener("message", (event) => {
        if (event.data?.type === "state-changed") callback()
      })
      return () => {
        try {
          channel?.close()
        } catch {
          // Focus/visibility/periodic revalidation remains available.
        }
      }
    } catch {
      try {
        channel?.close()
      } catch {
        // Fall through to the storage event subscription.
      }
    }
  }

  const onStorage = (event: StorageEvent) => {
    if (
      event.key === VIEW_AS_STATE_STORAGE_SIGNAL &&
      event.newValue === "state-changed"
    ) {
      callback()
    }
  }
  window.addEventListener("storage", onStorage)
  return () => window.removeEventListener("storage", onStorage)
}

export function resetViewAsBindingStateForTests() {
  expectedBinding = undefined
  bootstrapPromise = null
  hardBlocked = false
  reloadRequested = false
  clearStoredBinding()
}
