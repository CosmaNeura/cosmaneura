import type { DesktopSnapshot } from "./lib/desktop-state"

export interface SyntheticAppointment {
  id: string
  label: string
}

export const syntheticAppointments: SyntheticAppointment[] = [
  {
    id: "synthetic-visit-a",
    label: "Synthetic telehealth rehearsal · 9:00 AM",
  },
  {
    id: "synthetic-visit-b",
    label: "Synthetic consent drill · 2:30 PM",
  },
]

export const authorizedPreviewSnapshot: DesktopSnapshot = {
  phase: "authorized",
  appointmentSelection: "confirmed",
  consentStatus: "granted",
  localCaptureActive: false,
  backendReachable: true,
  providerEnabled: false,
  statusMessage: "Synthetic appointment and consent are ready for preview.",
}
