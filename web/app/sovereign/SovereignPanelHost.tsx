import { useEffect, useRef } from "react"

import { getClient } from "~/configuration/clerk"

import {
  SOVEREIGN_PANEL_ASSET_PATH,
  SOVEREIGN_PANEL_INTEGRITY,
} from "./SovereignPanelAsset"

type PanelContext = {
  clientId: string
  record_ref?: string
}

type PanelMountOptions = {
  sovereignUrl: string
  getAuthToken: () => Promise<string>
  getContext: () => Promise<PanelContext>
  shiftContent: boolean
}

type PanelInstance = {
  setRecord: (recordId: string) => Promise<unknown> | unknown
  destroy: () => void
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
}

const TEMPLATE_NAME = /^[A-Za-z0-9_-]{1,64}$/
// Fernet is urlsafe-base64. The host treats this as an opaque, short-lived
// grant; it never receives or parses the athena vendor patient identifier.
const OPAQUE_RECORD_GRANT = /^[A-Za-z0-9_-]{80,4096}={0,2}$/

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
    return { origin: parsed.origin, clerkTemplate: configuredTemplate }
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

function parseRecordRef(value: unknown): string {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    Object.keys(value).length !== 1 ||
    !("record_ref" in value) ||
    typeof value.record_ref !== "string" ||
    value.record_ref.length > 4096 ||
    !OPAQUE_RECORD_GRANT.test(value.record_ref)
  ) {
    throw new Error("Sandbox record response is invalid")
  }
  return value.record_ref
}

async function loadOpenRecordRef(
  appointmentId: string,
): Promise<string | null> {
  const sessionToken = await platformSessionToken()
  const response = await fetch(
    `/internal/v1/notes/${encodeURIComponent(appointmentId)}/ehr-labs-record`,
    {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${sessionToken}`,
      },
    },
  )
  if (response.status === 404) return null
  if (!response.ok) throw new Error("Sandbox record is unavailable")
  return parseRecordRef(await response.json())
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

    void loadPinnedPanel()
      .then(async (panel) => {
        if (cancelled) return
        const candidate = panel.mount({
          sovereignUrl: config.origin,
          getAuthToken: () => sovereignProviderToken(config.clerkTemplate),
          getContext: async () => {
            const recordRef = await loadOpenRecordRef(appointmentId)
            if (cancelled) {
              throw new Error("Open record changed while context was resolving")
            }
            return recordRef
              ? { clientId: appointmentId, record_ref: recordRef }
              : { clientId: appointmentId }
          },
          shiftContent: true,
        })
        mounted = candidate
        await candidate.setRecord(appointmentId)
        // Cleanup owns destruction once the effect has been cancelled. Do not
        // dereference `mounted` here: cleanup deliberately nulls it while an
        // asynchronous initial bind may still be resolving.
        if (cancelled) return
        panelRef.current = candidate
      })
      .catch(() => {
        const failed = mounted
        mounted = null
        if (panelRef.current === failed) panelRef.current = null
        failed?.destroy()
        // The Notes surface remains usable when the separately deployed panel
        // is unavailable. The panel itself renders network errors per turn.
      })

    return () => {
      cancelled = true
      panelRef.current = null
      const previous = mounted
      mounted = null
      previous?.destroy()
    }
  }, [appointmentId, config?.clerkTemplate, config?.origin])

  return null
}
