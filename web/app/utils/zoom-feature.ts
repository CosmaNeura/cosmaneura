// Zoom is gated to the shared per-environment beta allowlist (see
// beta-access.ts) — the same cohort the engagement "personality" badge uses.
// The server mirrors the production list in app/services/zoom_webhooks.py.
import { isBetaUserEnabled } from "./beta-access"

export function isZoomEnabled(email: string | null | undefined): boolean {
  return isBetaUserEnabled(email)
}
