import { useEffect, useRef } from "react"

import { getClient } from "~/configuration/clerk"

type SovereignContext = {
  clientId: string
}

type SovereignPanelMountOptions = {
  sovereignUrl: string
  getAuthToken: () => Promise<string>
  getContext: () => Promise<SovereignContext>
  shiftContent: boolean
}

type SovereignPanelInstance = {
  setRecord: (recordId: string) => Promise<unknown> | unknown
  destroy: () => void
}

type SovereignPanelApi = {
  mount: (options: SovereignPanelMountOptions) => SovereignPanelInstance
}

declare global {
  interface Window {
    CosmaNeuraPanel?: SovereignPanelApi
  }
}

export type SovereignPanelConfig = {
  origin: string
  clerkJwtTemplate: string
}

type ViteEnvironment = Record<string, string | boolean | undefined>

const PANEL_PATH = "/embed/v1/panel.js"

export function resolveSovereignPanelConfig(
  environment: ViteEnvironment = import.meta.env,
): SovereignPanelConfig | null {
  const configuredOrigin = environment.VITE_SOVEREIGN_URL
  const configuredTemplate = environment.VITE_SOVEREIGN_CLERK_JWT_TEMPLATE

  if (
    typeof configuredOrigin !== "string" ||
    typeof configuredTemplate !== "string"
  ) {
    return null
  }

  const template = configuredTemplate.trim()
  if (!template) return null

  try {
    const parsed = new URL(configuredOrigin.trim())
    const isOriginOnly =
      parsed.pathname === "/" && !parsed.search && !parsed.hash
    if (
      parsed.protocol !== "https:" ||
      parsed.username ||
      parsed.password ||
      !isOriginOnly
    ) {
      return null
    }

    return { origin: parsed.origin, clerkJwtTemplate: template }
  } catch {
    return null
  }
}

export function sovereignPanelScriptUrl(origin: string): string {
  return new URL(PANEL_PATH, origin).href
}

let panelLoad: Promise<SovereignPanelApi> | null = null
let panelLoadOrigin: string | null = null
let verifiedPanel: SovereignPanelApi | null = null
let verifiedPanelMount: SovereignPanelApi["mount"] | null = null

function loadSovereignPanel(origin: string): Promise<SovereignPanelApi> {
  if (window.CosmaNeuraPanel) {
    if (
      window.CosmaNeuraPanel === verifiedPanel &&
      window.CosmaNeuraPanel.mount === verifiedPanelMount &&
      panelLoadOrigin === origin
    ) {
      return Promise.resolve(window.CosmaNeuraPanel)
    }
    return Promise.reject(
      new Error("Refusing an unverified Sovereign panel global"),
    )
  }

  if (verifiedPanel) {
    return Promise.reject(new Error("Verified Sovereign panel was replaced"))
  }

  if (panelLoad) {
    if (panelLoadOrigin !== origin) {
      return Promise.reject(new Error("Sovereign panel origin changed"))
    }
    return panelLoad
  }

  panelLoadOrigin = origin
  const load = new Promise<SovereignPanelApi>((resolve, reject) => {
    const script = document.createElement("script")
    const rejectAndRemove = (error: Error) => {
      script.onload = null
      script.onerror = null
      script.remove()
      reject(error)
    }
    script.src = sovereignPanelScriptUrl(origin)
    script.async = true
    script.crossOrigin = "anonymous"
    script.referrerPolicy = "no-referrer"
    script.dataset.cosmaneuraSovereignPanel = "v1"
    script.onload = () => {
      const registeredPanel = window.CosmaNeuraPanel
      if (registeredPanel && typeof registeredPanel.mount === "function") {
        verifiedPanel = registeredPanel
        verifiedPanelMount = registeredPanel.mount
        script.onload = null
        script.onerror = null
        resolve(registeredPanel)
      } else {
        rejectAndRemove(
          new Error("Sovereign panel did not register its mount API"),
        )
      }
    }
    script.onerror = () =>
      rejectAndRemove(new Error("Sovereign panel failed to load"))
    document.head.appendChild(script)
  }).catch((error: unknown) => {
    panelLoad = null
    panelLoadOrigin = null
    throw error
  })
  panelLoad = load

  return load
}

export function SovereignPanelHost({ recordId }: { recordId: string }) {
  const config = resolveSovereignPanelConfig()
  const origin = config?.origin
  const clerkJwtTemplate = config?.clerkJwtTemplate
  const hasRecord = recordId.length > 0
  const recordIdRef = useRef(recordId)
  const panelRef = useRef<SovereignPanelInstance | null>(null)
  const destroyPanelRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    recordIdRef.current = recordId
    const panel = panelRef.current
    if (panel) {
      Promise.resolve()
        .then(() => panel.setRecord(recordId))
        .catch(() => {
          if (panelRef.current === panel) destroyPanelRef.current?.()
        })
    }
  }, [recordId])

  useEffect(() => {
    if (!origin || !clerkJwtTemplate || !hasRecord) return
    const panelOrigin = origin
    const tokenTemplate = clerkJwtTemplate

    let cancelled = false
    let mountedPanel: SovereignPanelInstance | null = null
    let panelDestroyed = false

    function destroyMountedPanel() {
      if (!mountedPanel || panelDestroyed) return
      const panel = mountedPanel
      panelDestroyed = true
      if (panelRef.current === panel) panelRef.current = null
      if (destroyPanelRef.current === destroyMountedPanel) {
        destroyPanelRef.current = null
      }
      try {
        panel.destroy()
      } catch {
        // Cleanup is best-effort after the rail has been detached from host
        // state; a remote component error must not break chart navigation.
      }
    }

    async function mountPanel() {
      const clerk = await getClient()
      if (cancelled || !clerk?.user || !clerk.session) return

      // Do not load or mount remote code until Clerk can mint the explicitly
      // configured provider token. A missing template/token disables the rail.
      const initialToken = await clerk.session.getToken({
        template: tokenTemplate,
      })
      if (cancelled || !initialToken) return

      const panelApi = await loadSovereignPanel(panelOrigin)
      if (cancelled) return

      mountedPanel = panelApi.mount({
        sovereignUrl: panelOrigin,
        getAuthToken: async () => {
          const currentClerk = await getClient()
          if (!currentClerk?.user || !currentClerk.session) {
            throw new Error("Provider session unavailable")
          }
          const token = await currentClerk.session.getToken({
            template: tokenTemplate,
          })
          if (!token) throw new Error("Provider token unavailable")
          return token
        },
        // The local patient TypeID isolates UI/thread state only. It is not an
        // Athena record reference and confers no EHR authority. Deliberately do
        // not return record_ref, patient identity, or chart content here.
        getContext: async () => ({ clientId: recordIdRef.current }),
        shiftContent: true,
        // This surface only renders completed notes. With no record-bound edit
        // API, onInsert/onSave are intentionally omitted so no inert action is
        // shown and no content can be written to a chart.
      })
      panelRef.current = mountedPanel
      destroyPanelRef.current = destroyMountedPanel
      try {
        await mountedPanel.setRecord(recordIdRef.current)
      } catch (error) {
        destroyMountedPanel()
        throw error
      }

      if (cancelled) {
        destroyMountedPanel()
      }
    }

    void mountPanel().catch(() => {
      // Fail closed: the host remains usable without exposing a partially
      // authenticated assistant when config, auth, or the remote asset fails.
    })

    return () => {
      cancelled = true
      destroyMountedPanel()
    }
  }, [clerkJwtTemplate, hasRecord, origin])

  return null
}
