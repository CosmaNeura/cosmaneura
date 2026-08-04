// The terminology setting (doctor/patient vs therapist/client vs
// counselor/client role labels) is gated to the shared per-environment beta
// allowlist (see beta-access.ts). Only the settings-page picker is gated —
// reading the label catalog is always safe because the stored value defaults
// to doctor/patient.
import { isBetaUserEnabled } from "./beta-access"

export function isTerminologyEnabled(
  email: string | null | undefined,
): boolean {
  return isBetaUserEnabled(email)
}
