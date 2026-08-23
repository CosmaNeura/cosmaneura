import { z } from "zod"

export const desktopPhaseSchema = z.enum([
  "idle",
  "waiting_for_browser",
  "authorized",
  "starting",
  "recording",
  "degraded",
  "stopping",
  "stopped",
  "error",
])

export const desktopSnapshotSchema = z
  .object({
    phase: desktopPhaseSchema,
    appointmentSelection: z.enum(["none", "confirmed"]),
    consentStatus: z.enum(["unknown", "granted"]),
    localCaptureActive: z.boolean(),
    backendReachable: z.boolean(),
    providerEnabled: z.boolean(),
    statusMessage: z.string().max(160),
  })
  .strict()

export type DesktopSnapshot = z.infer<typeof desktopSnapshotSchema>

export const idleDesktopSnapshot: DesktopSnapshot = {
  phase: "idle",
  appointmentSelection: "none",
  consentStatus: "unknown",
  localCaptureActive: false,
  backendReachable: true,
  providerEnabled: false,
  statusMessage: "Choose an appointment in the Cosmas provider platform.",
}

export function canStartCapture(
  snapshot: DesktopSnapshot,
  localConsentConfirmed: boolean,
): boolean {
  return (
    snapshot.phase === "authorized" &&
    snapshot.appointmentSelection === "confirmed" &&
    snapshot.consentStatus === "granted" &&
    snapshot.providerEnabled &&
    localConsentConfirmed
  )
}

export function canStopCapture(snapshot: DesktopSnapshot): boolean {
  return snapshot.localCaptureActive && snapshot.phase !== "stopping"
}
