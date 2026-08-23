import { useEffect, useRef } from "react"

import { getClient } from "~/configuration/clerk"

import {
  SOVEREIGN_PANEL_ASSET_PATH,
  SOVEREIGN_PANEL_INTEGRITY,
} from "./SovereignPanelAsset"

type PanelContext = {
  clientId: string
  thread_ref?: string
  context_snapshot_ref?: string
  digest_ref?: string
  terminology?: string
  care_model?: string
}

type HumanContactAction = {
  label: string
  action_id: string
  fallback_text: string
}

type AuthTokenRequest = {
  forceRefresh: true
}

type PanelMountOptions = {
  sovereignUrl: string
  embedVersion: "1.0"
  getAuthToken: (request?: AuthTokenRequest) => Promise<string>
  getContext: () => Promise<PanelContext>
  onHumanContact: (action: HumanContactAction) => void | Promise<void>
  shiftContent: boolean
}

type PanelInstance = {
  setRecord: (recordId: string | null) => Promise<unknown> | unknown
  destroy: () => void
}

type MountContextCredential = {
  contextToken: string
  issuedAt: number
  refreshAfter: number
  expiresAt: number
}

type MountContextTokenProvider = {
  getToken: (request?: AuthTokenRequest) => Promise<string>
  discard: () => void
}

type PanelApi = {
  mount: (options: PanelMountOptions) => PanelInstance
}

declare global {
  interface Window {
    CosmaNeuraPanel?: PanelApi
  }
}

type ViteEnvironment = Record<string, string | boolean | undefined>

export type SovereignProviderConfig = {
  origin: string
  clerkTemplate: string
  platformAuthorityEnabled: boolean
}

const TEMPLATE_NAME = /^[A-Za-z0-9_-]{1,64}$/
const EMBED_VERSION = "1.0" as const
const HUMAN_CONTACT_ACTION_ID = "human-contact"

class MountContextUnauthorizedError extends Error {}

function requestedForceRefresh(request: AuthTokenRequest | undefined): boolean {
  if (request === undefined) return false
  if (
    typeof request !== "object" ||
    request === null ||
    Array.isArray(request) ||
    Object.keys(request).join(",") !== "forceRefresh" ||
    request.forceRefresh !== true
  ) {
    throw new Error("Provider authority refresh request is invalid")
  }
  return true
}

export function resolveSovereignProviderConfig(
  environment: ViteEnvironment = import.meta.env,
): SovereignProviderConfig | null {
  const configuredOrigin = environment.VITE_SOVEREIGN_PROVIDER_URL
  const configuredTemplate = environment.VITE_SOVEREIGN_CLERK_TEMPLATE
  if (
    typeof configuredOrigin !== "string" ||
    typeof configuredTemplate !== "string" ||
    !TEMPLATE_NAME.test(configuredTemplate)
  ) {
    return null
  }

  try {
    const parsed = new URL(configuredOrigin.trim())
    if (
      parsed.protocol !== "https:" ||
      parsed.username ||
      parsed.password ||
      parsed.pathname !== "/" ||
      parsed.search ||
      parsed.hash
    ) {
      return null
    }
    return {
      origin: parsed.origin,
      clerkTemplate: configuredTemplate,
      platformAuthorityEnabled:
        environment.VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED === "true",
    }
  } catch {
    return null
  }
}

let panelLoad: Promise<PanelApi> | null = null
let verifiedPanel: PanelApi | null = null
let verifiedMount: PanelApi["mount"] | null = null

function loadPinnedPanel(): Promise<PanelApi> {
  if (window.CosmaNeuraPanel) {
    if (
      window.CosmaNeuraPanel === verifiedPanel &&
      window.CosmaNeuraPanel.mount === verifiedMount
    ) {
      return Promise.resolve(window.CosmaNeuraPanel)
    }
    return Promise.reject(new Error("Refusing an unverified panel global"))
  }
  if (verifiedPanel) {
    return Promise.reject(new Error("The verified panel global was removed"))
  }
  if (panelLoad) return panelLoad

  panelLoad = new Promise<PanelApi>((resolve, reject) => {
    const script = document.createElement("script")
    const rejectAndRemove = (error: Error) => {
      script.onload = null
      script.onerror = null
      script.remove()
      reject(error)
    }

    script.src = SOVEREIGN_PANEL_ASSET_PATH
    script.integrity = SOVEREIGN_PANEL_INTEGRITY
    script.crossOrigin = "anonymous"
    script.referrerPolicy = "no-referrer"
    script.async = true
    script.dataset.cosmaneuraSovereignPanel = "v1"
    script.onload = () => {
      const registered = window.CosmaNeuraPanel
      if (!registered || typeof registered.mount !== "function") {
        rejectAndRemove(new Error("Pinned panel did not register its API"))
        return
      }
      verifiedPanel = registered
      verifiedMount = registered.mount
      script.onload = null
      script.onerror = null
      resolve(registered)
    }
    script.onerror = () =>
      rejectAndRemove(new Error("Pinned panel failed to load"))
    document.head.appendChild(script)
  }).catch((error: unknown) => {
    panelLoad = null
    throw error
  })

  return panelLoad
}

async function platformSessionToken(): Promise<string> {
  const clerk = await getClient()
  if (!clerk?.user || !clerk.session) {
    throw new Error("Provider session unavailable")
  }
  const token = await clerk.session.getToken()
  if (!token) throw new Error("Provider session unavailable")
  return token
}

async function sovereignProviderToken(template: string): Promise<string> {
  const clerk = await getClient()
  if (!clerk?.user || !clerk.session) {
    throw new Error("Provider session unavailable")
  }
  const token = await clerk.session.getToken({ template })
  if (!token) throw new Error("Provider assistant token unavailable")
  return token
}

function parseMountContextCredential(
  value: unknown,
  now: number,
): MountContextCredential {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    Object.keys(value).sort().join(",") !==
      "context_token,expires_at,issued_at,refresh_after,token_type" ||
    !("context_token" in value) ||
    typeof value.context_token !== "string" ||
    value.context_token.length < 16 ||
    value.context_token.length > 8192 ||
    /\s/.test(value.context_token) ||
    !("token_type" in value) ||
    value.token_type !== "Bearer" ||
    !("issued_at" in value) ||
    typeof value.issued_at !== "string" ||
    !("refresh_after" in value) ||
    typeof value.refresh_after !== "string" ||
    !("expires_at" in value) ||
    typeof value.expires_at !== "string"
  ) {
    throw new Error("Provider authority response is invalid")
  }
  const issuedAt = Date.parse(value.issued_at)
  const refreshAfter = Date.parse(value.refresh_after)
  const expiresAt = Date.parse(value.expires_at)
  if (
    !Number.isFinite(issuedAt) ||
    !Number.isFinite(refreshAfter) ||
    !Number.isFinite(expiresAt) ||
    issuedAt > now + 60_000 ||
    issuedAt >= refreshAfter ||
    refreshAfter >= expiresAt ||
    now >= refreshAfter
  ) {
    throw new Error("Provider authority response is invalid")
  }
  return {
    contextToken: value.context_token,
    issuedAt,
    refreshAfter,
    expiresAt,
  }
}

async function requestProviderMountContext(
  endpoint: "exchange" | "rotate",
  authorizationToken: string,
  appointmentId: string,
  signal: AbortSignal,
): Promise<MountContextCredential> {
  const response = await fetch(`/embed/v1/context-tokens/${endpoint}`, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
    signal,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${authorizationToken}`,
      "Content-Type": "application/json",
      "X-Cosmaneura-Embed-Version": EMBED_VERSION,
    },
    body: JSON.stringify({
      requested_surface: "provider_assistant",
      record_ref: appointmentId,
    }),
  })
  if (response.status === 401) throw new MountContextUnauthorizedError()
  if (!response.ok) throw new Error("Provider authority is unavailable")
  if (response.headers.get("X-Cosmaneura-Embed-Version") !== EMBED_VERSION) {
    throw new Error("Provider authority version is invalid")
  }
  return parseMountContextCredential(await response.json(), Date.now())
}

function createProviderMountContextTokenProvider(
  appointmentId: string,
): MountContextTokenProvider {
  let credential: MountContextCredential | null = null
  let pending: {
    controller: AbortController
    promise: Promise<string>
    serial: number
  } | null = null
  let requestSerial = 0
  let discarded = false

  const clearPending = (serial: number): void => {
    if (pending?.serial === serial) pending = null
  }

  const getToken = async (request?: AuthTokenRequest): Promise<string> => {
    const forceRefresh = requestedForceRefresh(request)
    if (discarded) throw new Error("Provider authority is unavailable")
    const now = Date.now()
    if (pending) {
      if (forceRefresh && !credential) {
        throw new Error("Provider authority is unavailable")
      }
      return pending.promise
    }
    if (forceRefresh && !credential) {
      // A forced refresh is an exact retry within an existing logical mount
      // session. It must never create a replacement session by exchanging.
      throw new Error("Provider authority is unavailable")
    }
    if (!forceRefresh && credential && now < credential.refreshAfter) {
      return credential.contextToken
    }
    if (credential && now >= credential.expiresAt) {
      // A new exchange would create a different logical mount session and
      // break exact client-turn replay. Only a new record generation may
      // exchange a replacement session.
      credential = null
      discarded = true
      throw new Error("Provider authority is unavailable")
    }

    const current = credential
    const controller = new AbortController()
    const serial = ++requestSerial
    const operation = (async () => {
      try {
        const next = current
          ? await requestProviderMountContext(
              "rotate",
              current.contextToken,
              appointmentId,
              controller.signal,
            )
          : await requestProviderMountContext(
              "exchange",
              await platformSessionToken(),
              appointmentId,
              controller.signal,
            )
        if (discarded) throw new Error("Provider authority is unavailable")
        credential = next
        return next.contextToken
      } catch (error) {
        if (error instanceof MountContextUnauthorizedError) {
          credential = null
          discarded = true
          throw new Error("Provider authority is unavailable")
        }
        throw error
      } finally {
        clearPending(serial)
      }
    })()
    pending = { controller, promise: operation, serial }
    return operation
  }

  return {
    getToken,
    discard: () => {
      discarded = true
      credential = null
      pending?.controller.abort()
      pending = null
    },
  }
}

function openHumanContact(action: HumanContactAction): void {
  if (
    typeof action !== "object" ||
    action === null ||
    Array.isArray(action) ||
    Object.keys(action).sort().join(",") !== "action_id,fallback_text,label" ||
    action.action_id !== HUMAN_CONTACT_ACTION_ID
  ) {
    throw new Error("Human contact action is unavailable")
  }
  const opened = window.open(
    "/support#contact",
    "_blank",
    "noopener,noreferrer",
  )
  if (opened === null) throw new Error("Human contact action is unavailable")
}

function resetAndDestroy(panel: PanelInstance): void {
  try {
    void Promise.resolve(panel.setRecord(null)).catch(() => undefined)
  } finally {
    panel.destroy()
  }
}

export function SovereignPanelHost({
  appointmentId,
}: {
  appointmentId: string
}) {
  const config = resolveSovereignProviderConfig()
  const panelRef = useRef<PanelInstance | null>(null)

  useEffect(() => {
    if (!config) return
    let cancelled = false
    let mounted: PanelInstance | null = null
    const mountTokenProvider = config.platformAuthorityEnabled
      ? createProviderMountContextTokenProvider(appointmentId)
      : null

    void loadPinnedPanel()
      .then(async (panel) => {
        if (cancelled) return
        const candidate = panel.mount({
          sovereignUrl: config.origin,
          embedVersion: EMBED_VERSION,
          getAuthToken: (request) =>
            mountTokenProvider
              ? mountTokenProvider.getToken(request)
              : sovereignProviderToken(config.clerkTemplate),
          getContext: async () => {
            if (cancelled) {
              throw new Error("Open record changed while context was resolving")
            }
            // No legacy raw history, note, patient, or labs grant crosses this
            // boundary. Governed opaque refs can be added only when the platform
            // exposes their ratified record-scoped read contract.
            return { clientId: appointmentId }
          },
          onHumanContact: openHumanContact,
          // loadHistory is deliberately omitted until the platform exposes the
          // governed HydratedHistory read operation.
          shiftContent: true,
        })
        mounted = candidate
        const binding = await candidate.setRecord(appointmentId)
        if (binding === null) {
          throw new Error("Provider record binding is unavailable")
        }
        // Cleanup owns destruction once the effect has been cancelled. Do not
        // dereference `mounted` here: cleanup deliberately nulls it while an
        // asynchronous initial bind may still be resolving.
        if (cancelled) return
        panelRef.current = candidate
      })
      .catch(() => {
        mountTokenProvider?.discard()
        const failed = mounted
        mounted = null
        if (panelRef.current === failed) panelRef.current = null
        if (failed) resetAndDestroy(failed)
        // The Notes surface remains usable when the separately deployed panel
        // is unavailable. The panel itself renders network errors per turn.
      })

    return () => {
      cancelled = true
      mountTokenProvider?.discard()
      panelRef.current = null
      const previous = mounted
      mounted = null
      if (previous) resetAndDestroy(previous)
    }
  }, [
    appointmentId,
    config?.clerkTemplate,
    config?.origin,
    config?.platformAuthorityEnabled,
  ])

  return null
}
