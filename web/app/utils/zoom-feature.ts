import { isStaging } from "~/utils/environment"

const ZOOM_BETA_EMAILS = new Set([
  "dev@cosmaneura.com",
  "zoom-reviewer@cosmaneura.com",
])

// In staging the Zoom feature is restricted to a single dedicated test account.
// A doctor's real Zoom account may be connected in both staging and production
// (separate Zoom apps), which would send two bots to the same real meeting, so
// staging only ever exercises Zoom through this internal account. The server
// enforces the same allowlist in app/services/zoom_webhooks.py.
const STAGING_ZOOM_EMAILS = new Set(["zoom-reviewer@cosmaneura.com"])

export function isZoomEnabled(email: string | null | undefined): boolean {
  if (!email) return false
  const allowlist = isStaging() ? STAGING_ZOOM_EMAILS : ZOOM_BETA_EMAILS
  return allowlist.has(email.toLowerCase())
}
