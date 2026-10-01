import { type ReactNode, createContext, useContext } from "react"

import type { VisitPrepSettingsData } from "~/configuration/client"

const unavailableAccess: VisitPrepSettingsData = {
  eligible: false,
  can_manage: false,
  can_send_email: false,
  can_send_sms: false,
}

const VisitPrepAccessContext =
  createContext<VisitPrepSettingsData>(unavailableAccess)

export function VisitPrepAccessProvider({
  access,
  children,
}: {
  access: VisitPrepSettingsData | null
  children: ReactNode
}) {
  return (
    <VisitPrepAccessContext.Provider value={access ?? unavailableAccess}>
      {children}
    </VisitPrepAccessContext.Provider>
  )
}

export function useVisitPrepAccess(): VisitPrepSettingsData {
  return useContext(VisitPrepAccessContext)
}
