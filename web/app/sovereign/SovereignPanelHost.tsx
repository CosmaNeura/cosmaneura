import { useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"

import { getClient, useBrowserPrincipal } from "~/configuration/clerk"
import type { PriorNoteOverview } from "~/configuration/client"
import { appointmentDateFormat } from "~/lib/date"
import { withReadDeadline } from "~/lib/read-deadline"
import { getViewAsStatus } from "~/lib/view-as-api"

import {
  type CitationDisplay,
  type CitationDisplayOptions,
  citationHref,
  loadCitationDisplay,
} from "./SovereignCitationDisplay"
import {
  HistoryAccessDeniedError,
  type SovereignHistory,
  loadSovereignHistory,
} from "./SovereignHistory"
import {
  SOVEREIGN_PANEL_ASSET_PATH,
  SOVEREIGN_PANEL_INTEGRITY,
} from "./SovereignPanelAsset"

type PanelContext = {
  clientId: string
  thread_ref?: string
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

export type SessionPrepUiState =
  | "ready"
  | "refreshing"
  | "pending"
  | "unavailable"
  | "update_needed"

type PanelMountOptions = {
  sovereignUrl: string
  embedVersion: "1.0"
  getAuthToken: (request?: AuthTokenRequest) => Promise<string>
  getContext: () => Promise<PanelContext>
  onHumanContact: (action: HumanContactAction) => void | Promise<void>
  onAccessChange: (granted: boolean) => void
  loadHistory: (
    recordId: string,
    options: { signal: AbortSignal },
  ) => Promise<SovereignHistory>
  loadCitationHistory: (
    recordId: string,
    options: CitationDisplayOptions,
  ) => Promise<CitationDisplay>
  citationHref: (reference: string) => string
  onSessionPrepStateChange?: (state: SessionPrepUiState) => void
  shiftContent: boolean
  fabBottom?: string
}

type PanelInstance = {
  setRecord: (recordId: string | null) => Promise<unknown> | unknown
  destroy: () => void
  openSessionPrep?: () => Promise<unknown> | unknown
  invalidateSessionPrep?: (recordId: string) => void
  recordPresentationTarget?: HTMLElement | null
}

type MountContextCredential = {
  contextToken: string
  issuedAt: number
  refreshAfter: number
  expiresAt: number
}

type AuthenticationReader = <T>(
  read: () => Promise<T>,
  signal?: AbortSignal,
) => Promise<T>

type MountContextTokenProvider = {
  getToken: (
    request?: AuthTokenRequest,
    signal?: AbortSignal,
  ) => Promise<string>
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
}

const EMBED_VERSION = "1.0" as const
const HUMAN_CONTACT_ACTION_ID = "human-contact"
const PROVIDER_V7_FAB_BOTTOM = "84px"
const PROVIDER_V7_LAYER_STYLE = `
.cnp-root > .cnp-rail { z-index: 100001 !important; }
.cnp-root > .cnp-tip { z-index: 100002 !important; }
`

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
  if (environment.VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED !== "true") {
    return null
  }
  const configuredOrigin = environment.VITE_SOVEREIGN_PROVIDER_URL
  if (typeof configuredOrigin !== "string") {
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
    }
  } catch {
    return null
  }
}

type PendingPanelLoad = {
  promise: Promise<PanelApi>
  consumers: number
  cancel: () => void
}

let panelLoad: PendingPanelLoad | null = null
let verifiedPanel: PanelApi | null = null
let verifiedMount: PanelApi["mount"] | null = null

function startPinnedPanelLoad(): PendingPanelLoad {
  let resolveLoad!: (panel: PanelApi) => void
  let rejectLoad!: (error: Error) => void
  const load: PendingPanelLoad = {
    promise: new Promise<PanelApi>((resolve, reject) => {
      resolveLoad = resolve
      rejectLoad = reject
    }),
    consumers: 0,
    cancel: () => rejectAndRemove(new DOMException("Cancelled", "AbortError")),
  }
  const script = document.createElement("script")
  const detach = () => {
    script.onload = null
    script.onerror = null
  }
  const rejectAndRemove = (error: Error) => {
    if (panelLoad !== load) return
    panelLoad = null
    detach()
    script.remove()
    rejectLoad(error)
  }

  script.src = SOVEREIGN_PANEL_ASSET_PATH
  script.integrity = SOVEREIGN_PANEL_INTEGRITY
  script.crossOrigin = "anonymous"
  script.referrerPolicy = "no-referrer"
  script.async = true
  script.dataset.cosmaneuraSovereignPanel = "v1"
  script.onload = () => {
    if (panelLoad !== load) return
    const registered = window.CosmaNeuraPanel
    if (!registered || typeof registered.mount !== "function") {
      rejectAndRemove(new Error("Pinned panel did not register its API"))
      return
    }
    verifiedPanel = registered
    verifiedMount = registered.mount
    panelLoad = null
    detach()
    resolveLoad(registered)
  }
  script.onerror = () =>
    rejectAndRemove(new Error("Pinned panel failed to load"))
  panelLoad = load
  document.head.appendChild(script)
  return load
}

function loadPinnedPanel(signal: AbortSignal): Promise<PanelApi> {
  if (signal.aborted) {
    return Promise.reject(new DOMException("Cancelled", "AbortError"))
  }
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
  const load = panelLoad ?? startPinnedPanelLoad()
  load.consumers += 1
  return new Promise<PanelApi>((resolve, reject) => {
    let released = false
    const release = () => {
      if (released) return
      released = true
      signal.removeEventListener("abort", cancel)
      load.consumers -= 1
      // One departing host cannot cancel a load still needed by another.
      if (load.consumers === 0) load.cancel()
    }
    const cancel = () => {
      release()
      reject(new DOMException("Cancelled", "AbortError"))
    }
    load.promise.then(
      (panel) => {
        release()
        resolve(panel)
      },
      (error: unknown) => {
        release()
        reject(error)
      },
    )
    signal.addEventListener("abort", cancel, { once: true })
    if (signal.aborted) cancel()
  })
}

async function platformSessionToken(
  checkPrincipal: (signal?: AbortSignal) => Promise<void>,
  authenticate: AuthenticationReader,
  signal?: AbortSignal,
): Promise<string> {
  await checkPrincipal(signal)
  const clerk = await authenticate(() => getClient(), signal)
  await checkPrincipal(signal)
  if (!clerk?.user || !clerk.session) {
    throw new Error("Provider session unavailable")
  }
  const session = clerk.session
  const token = await authenticate(() => session.getToken(), signal)
  if (!token) throw new Error("Provider session unavailable")
  await checkPrincipal(signal)
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
    !value.context_token.startsWith("mctx_") ||
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
  checkPrincipal: (signal?: AbortSignal) => Promise<void>,
  authenticate: AuthenticationReader,
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

  const getToken = async (
    request?: AuthTokenRequest,
    signal?: AbortSignal,
  ): Promise<string> => {
    const forceRefresh = requestedForceRefresh(request)
    await checkPrincipal(signal)
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
    const abortRead = () => controller.abort(signal?.reason)
    signal?.addEventListener("abort", abortRead, { once: true })
    if (signal?.aborted) abortRead()
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
              await platformSessionToken(checkPrincipal, authenticate, signal),
              appointmentId,
              controller.signal,
            )
        await checkPrincipal(signal)
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
        signal?.removeEventListener("abort", abortRead)
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
  patientName,
  appointmentDate,
  priorNotes,
  openRequest = 0,
  onSessionPrepStateChange,
}: {
  appointmentId: string
  patientName?: string
  appointmentDate?: string
  priorNotes?: PriorNoteOverview | null
  openRequest?: number
  onSessionPrepStateChange?: (state: SessionPrepUiState) => void
}) {
  const config = resolveSovereignProviderConfig()
  const { isLoaded, userId, sessionId, identity } = useBrowserPrincipal()
  const panelRef = useRef<PanelInstance | null>(null)
  const [panelReady, setPanelReady] = useState(false)
  const [presentationTarget, setPresentationTarget] =
    useState<HTMLElement | null>(null)
  const stateCallback = useRef(onSessionPrepStateChange)
  stateCallback.current = onSessionPrepStateChange
  const sourceSnapshot = useRef<{
    appointmentId: string
    fingerprint: string
  } | null>(null)
  const lastOpenRequest = useRef(0)
  const includedNotes =
    priorNotes?.notes.filter((note) => note.status === "included") ?? []
  // This value only invalidates presentation. It is never passed to authority,
  // getContext, or a request; Core still selects and authorizes every source.
  const sourceFingerprint =
    priorNotes?.selected_source_fingerprint ??
    JSON.stringify(
      includedNotes.map((note) => [note.note_id, note.appointment_date]).sort(),
    )

  useLayoutEffect(() => {
    if (!panelReady) return
    const previous = sourceSnapshot.current
    sourceSnapshot.current = { appointmentId, fingerprint: sourceFingerprint }
    if (
      previous?.appointmentId === appointmentId &&
      previous.fingerprint !== sourceFingerprint
    )
      panelRef.current?.invalidateSessionPrep?.(appointmentId)
  }, [appointmentId, panelReady, sourceFingerprint, priorNotes])

  useLayoutEffect(() => {
    if (!panelReady || openRequest <= lastOpenRequest.current) return
    lastOpenRequest.current = openRequest
    void Promise.resolve(panelRef.current?.openSessionPrep?.()).catch(
      () => undefined,
    )
  }, [panelReady, openRequest])
  const [mountAttempt, setMountAttempt] = useState(0)
  const [recovery, setRecovery] = useState<"unavailable" | "connecting" | null>(
    null,
  )

  useLayoutEffect(() => {
    if (!config) return
    setPanelReady(false)
    setPresentationTarget(null)
    sourceSnapshot.current = null
    if (!isLoaded || !userId || !sessionId) {
      setRecovery("unavailable")
      return
    }
    let cancelled = false
    let accessWasGranted = false
    let accessLost = false
    let mounted: PanelInstance | null = null
    const statusController = new AbortController()
    const readAuthentication = new Map<
      AbortSignal,
      { pending: number; failed: boolean }
    >()
    const authenticate: AuthenticationReader = async (read, signal) => {
      const state = signal ? readAuthentication.get(signal) : undefined
      if (state) state.pending++
      try {
        return await read()
      } catch (error) {
        if (state) state.failed = true
        throw error
      } finally {
        if (state) state.pending--
      }
    }
    const mountTokenProvider = createProviderMountContextTokenProvider(
      appointmentId,
      checkPrincipal,
      authenticate,
    )
    setRecovery(mountAttempt > 0 ? "connecting" : null)
    const finishBootstrap = () => clearTimeout(bootstrapTimeout)
    const discardMounted = () => {
      cancelled = true
      finishBootstrap()
      statusController.abort()
      mountTokenProvider.discard()
      setPanelReady(false)
      setPresentationTarget(null)
      const previous = mounted
      mounted = null
      if (panelRef.current === previous) panelRef.current = null
      if (previous) resetAndDestroy(previous)
    }
    let readSdkPrincipal:
      | (() => ReturnType<typeof identity.current>)
      | undefined
    const matches = (principal: ReturnType<typeof identity.current>) =>
      principal.isLoaded &&
      principal.userId === userId &&
      principal.sessionId === sessionId
    function assertPrincipal() {
      if (cancelled || accessLost)
        throw new Error("Provider authority is unavailable")
      if (
        !matches(identity.current()) ||
        (readSdkPrincipal && !matches(readSdkPrincipal()))
      ) {
        discardMounted()
        setRecovery("unavailable")
        throw new DOMException("Provider principal changed", "AbortError")
      }
    }
    async function checkPrincipal(signal?: AbortSignal) {
      signal?.throwIfAborted()
      assertPrincipal()
      readSdkPrincipal = await authenticate(
        () => identity.readCurrent(),
        signal,
      )
      signal?.throwIfAborted()
      assertPrincipal()
    }
    // Bound the whole handshake, including script loading, authority exchange,
    // and welcome. A timed-out generation can never complete a later retry.
    const bootstrapTimeout = setTimeout(() => {
      if (cancelled) return
      discardMounted()
      setRecovery("unavailable")
    }, 30_000)

    const unsubscribeAuth = identity.subscribe((principal) => {
      if (!matches(principal)) {
        discardMounted()
        setRecovery("unavailable")
      }
    })

    // Fail closed before loading the platform context-token path. A Clerk
    // session token is accepted only by the same-origin exchange endpoint and
    // is never handed to the embedded panel as a delegation credential.
    void getViewAsStatus(statusController.signal)
      .then((viewAs) => {
        if (cancelled) return null
        if (viewAs.active) {
          finishBootstrap()
          if (mountAttempt > 0) setRecovery("unavailable")
          return null
        }
        return loadPinnedPanel(statusController.signal)
      })
      .then(async (panel) => {
        if (cancelled || panel === null) return
        const candidate = panel.mount({
          sovereignUrl: config.origin,
          embedVersion: EMBED_VERSION,
          getAuthToken: (request) => mountTokenProvider.getToken(request),
          getContext: async () => {
            if (cancelled || accessLost)
              throw new Error("Open record changed while context was resolving")
            await checkPrincipal()
            if (cancelled || accessLost) {
              throw new Error("Open record changed while context was resolving")
            }
            // The record-bound mount token is the only authority input. No raw
            // history, note, patient, labs content, or Session Prep handle
            // crosses this browser bridge.
            return { clientId: appointmentId }
          },
          onHumanContact: openHumanContact,
          onSessionPrepStateChange: (state) => {
            if (!cancelled && !accessLost) stateCallback.current?.(state)
          },
          loadHistory: async (recordId, { signal }) => {
            const controller = new AbortController()
            const authentication = { pending: 0, failed: false }
            let readSignal: AbortSignal | undefined
            const abort = () => controller.abort()
            const signals = [signal, statusController.signal]
            signals.forEach((source) =>
              source.addEventListener("abort", abort, { once: true }),
            )
            if (signals.some((source) => source.aborted)) abort()
            const assertCurrent = () => {
              assertPrincipal()
              controller.signal.throwIfAborted()
              if (cancelled || accessLost || recordId !== appointmentId) {
                throw new DOMException("Open record changed", "AbortError")
              }
            }
            try {
              return await withReadDeadline(
                async (signal) => {
                  readSignal = signal
                  readAuthentication.set(signal, authentication)
                  const check = () => {
                    signal.throwIfAborted()
                    assertCurrent()
                  }
                  check()
                  await checkPrincipal(signal)
                  check()
                  const history = await loadSovereignHistory({
                    origin: config.origin,
                    getToken: () =>
                      mountTokenProvider.getToken(undefined, signal),
                    signal,
                    assertCurrent: check,
                  })
                  await checkPrincipal(signal)
                  check()
                  return history
                },
                { signal: controller.signal },
              )
            } catch (error) {
              if (authentication.pending > 0 || authentication.failed) {
                if (!cancelled && !accessLost) {
                  discardMounted()
                  setRecovery("unavailable")
                }
                const denied = new Error(
                  "Assistant access is unavailable",
                ) as Error & { authenticationUnavailable: boolean }
                denied.name = error instanceof Error ? error.name : "Error"
                denied.authenticationUnavailable = true
                throw denied
              }
              // A verified transport/map failure keeps its existing fallback,
              // but cannot bypass a synchronously changed live/SDK principal.
              if (controller.signal.aborted) throw error
              assertPrincipal()
              if (
                !cancelled &&
                !accessLost &&
                error instanceof HistoryAccessDeniedError
              ) {
                discardMounted()
                setRecovery("unavailable")
              }
              throw error
            } finally {
              if (readSignal) readAuthentication.delete(readSignal)
              signals.forEach((source) =>
                source.removeEventListener("abort", abort),
              )
            }
          },
          citationHref,
          loadCitationHistory: async (recordId, options) => {
            const controller = new AbortController()
            const authentication = { pending: 0, failed: false }
            let readSignal: AbortSignal | undefined
            const abort = () => controller.abort()
            const signals = [options.signal, statusController.signal]
            signals.forEach((source) =>
              source.addEventListener("abort", abort, { once: true }),
            )
            if (signals.some((source) => source.aborted)) abort()
            const assertCurrent = () => {
              assertPrincipal()
              controller.signal.throwIfAborted()
              if (cancelled || accessLost || recordId !== appointmentId)
                throw new DOMException("Open record changed", "AbortError")
            }
            try {
              return await withReadDeadline(
                async (signal) => {
                  readSignal = signal
                  readAuthentication.set(signal, authentication)
                  const check = () => {
                    signal.throwIfAborted()
                    assertCurrent()
                  }
                  check()
                  await checkPrincipal(signal)
                  check()
                  const display = await loadCitationDisplay({
                    origin: config.origin,
                    getToken: () =>
                      mountTokenProvider.getToken(undefined, signal),
                    assertCurrent: check,
                    options: { ...options, signal },
                  })
                  await checkPrincipal(signal)
                  check()
                  return display
                },
                { signal: controller.signal },
              )
            } catch (error) {
              if (authentication.pending > 0 || authentication.failed) {
                if (!cancelled && !accessLost) {
                  discardMounted()
                  setRecovery("unavailable")
                }
                const denied = new Error(
                  "Assistant access is unavailable",
                ) as Error & { authenticationUnavailable: boolean }
                denied.name = error instanceof Error ? error.name : "Error"
                denied.authenticationUnavailable = true
                throw denied
              }
              // A verified transport/map failure keeps its existing fallback,
              // but cannot bypass a synchronously changed live/SDK principal.
              if (controller.signal.aborted) throw error
              assertPrincipal()
              if (
                !cancelled &&
                !accessLost &&
                error instanceof HistoryAccessDeniedError
              ) {
                discardMounted()
                setRecovery("unavailable")
                const denied = new Error(
                  "Assistant access is unavailable",
                ) as Error & { authenticationUnavailable: boolean }
                denied.authenticationUnavailable = true
                throw denied
              }
              throw error
            } finally {
              if (readSignal) readAuthentication.delete(readSignal)
              signals.forEach((source) =>
                source.removeEventListener("abort", abort),
              )
            }
          },
          onAccessChange: (granted) => {
            if (cancelled || accessLost) return
            if (granted) {
              accessWasGranted = true
            } else if (accessWasGranted) {
              // The old generation stays terminal. Only the explicit host
              // action below may create a new authenticated mount; no old
              // request, turn ID, draft or preparation is replayed into it.
              accessLost = true
              finishBootstrap()
              statusController.abort()
              mountTokenProvider.discard()
              setPanelReady(false)
              setPresentationTarget(null)
              setRecovery("unavailable")
            }
          },
          shiftContent: true,
          // Keep the V7 launcher above the dashboard's existing support pill.
          fabBottom: PROVIDER_V7_FAB_BOTTOM,
        })
        mounted = candidate
        const binding = await candidate.setRecord(appointmentId)
        if (binding === null) {
          throw new Error("Provider record binding is unavailable")
        }
        // Cleanup owns destruction once the effect has been cancelled. Do not
        // dereference `mounted` here: cleanup deliberately nulls it while an
        // asynchronous initial bind may still be resolving.
        await checkPrincipal()
        if (cancelled || accessLost) return
        finishBootstrap()
        panelRef.current = candidate
        setPresentationTarget(candidate.recordPresentationTarget ?? null)
        setPanelReady(true)
        setRecovery(null)
      })
      .catch(() => {
        if (cancelled) return
        discardMounted()
        setRecovery("unavailable")
        // The Notes surface remains usable when the separately deployed panel
        // is unavailable. The panel itself renders network errors per turn.
      })

    return () => {
      unsubscribeAuth()
      discardMounted()
    }
  }, [
    appointmentId,
    config?.origin,
    mountAttempt,
    identity,
    isLoaded,
    userId,
    sessionId,
  ])

  return config ? (
    <>
      <style data-sovereign-provider-v7-layer>{PROVIDER_V7_LAYER_STYLE}</style>
      {presentationTarget &&
        (patientName ||
          priorNotes?.newer_prior_note_awaiting_review === true) &&
        createPortal(
          <div className="py-3">
            {patientName && <p className="font-semibold">{patientName}</p>}
            {appointmentDate && (
              <p>Visit · {appointmentDateFormat(appointmentDate)}</p>
            )}
            {priorNotes?.newer_prior_note_awaiting_review === true && (
              <p
                role="status"
                className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
              >
                A newer prior-visit note is awaiting your review. Session Prep
                still shows earlier reviewed history. Review the newer note,
                then refresh Session Prep to include it.
              </p>
            )}
            {includedNotes.length > 0 && (
              <details className="mt-2">
                <summary className="cursor-pointer">
                  Reviewed sources available · {includedNotes.length}
                </summary>
                <ul className="mt-2 space-y-2">
                  {includedNotes.map((note) => (
                    <li key={note.note_id}>
                      <a
                        href={`/notes/${encodeURIComponent(note.appointment_id)}?reviewNote=${encodeURIComponent(note.note_id)}#note-${encodeURIComponent(note.note_id)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {note.note_type} ·{" "}
                        {appointmentDateFormat(note.appointment_date)}
                        <span className="sr-only">
                          {" "}
                          · Open reviewed source in a new tab
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-xs">
                  Available reviewed notes; the brief states the source coverage
                  used in its last update.
                </p>
              </details>
            )}
          </div>,
          presentationTarget,
        )}
      {recovery && (
        <div
          className="bg-background text-foreground fixed right-[18px] z-[100002] flex max-w-[calc(100vw-36px)] items-center gap-3 rounded-lg border p-3 shadow-lg"
          style={{ bottom: PROVIDER_V7_FAB_BOTTOM }}
        >
          <p className="text-sm" role="status">
            {recovery === "connecting"
              ? "Checking assistant access…"
              : "Assistant session unavailable."}
          </p>
          <button
            type="button"
            className="rounded-md border px-3 py-2 text-sm disabled:opacity-50"
            disabled={recovery === "connecting"}
            onClick={() => {
              if (recovery !== "unavailable") return
              setRecovery("connecting")
              setMountAttempt((attempt) => attempt + 1)
            }}
          >
            {recovery === "connecting"
              ? "Reconnecting assistant…"
              : "Reconnect assistant"}
          </button>
        </div>
      )}
    </>
  ) : null
}
