// Session Prep (the pre-session brief) is gated to the shared per-environment
// beta allowlist (see beta-access.ts). The server enforces its own allowlist
// in app/services/session_prep_gate.py — keep the two in sync, otherwise the
// card renders but every request 404s.
import { isBetaUserEnabled } from "./beta-access"

export function isSessionPrepEnabled(
  email: string | null | undefined,
): boolean {
  return isBetaUserEnabled(email)
}
