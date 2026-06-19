// The engagement-role ("personality") badge on the note detail page is gated to
// the same shared per-environment beta allowlist as Zoom (see beta-access.ts),
// so in production only the explicit beta accounts can see it.
import { isBetaUserEnabled } from "./beta-access"

export function isEngagementEnabled(email: string | null | undefined): boolean {
  return isBetaUserEnabled(email)
}
