import { isBetaUserEnabled } from "./beta-access"

const STAGING_API_HOST = "staging-app.cosmaneura.com"

function pointsAtStagingApi(value: string | undefined): boolean {
  if (!value) return false

  try {
    const url = new URL(value)
    return url.protocol === "https:" && url.host === STAGING_API_HOST
  } catch {
    return false
  }
}

/**
 * Permit an authenticated pilot to finish onboarding without Zoom OAuth.
 *
 * This is deliberately fail-closed at three independent gates: the build-time
 * switch must be explicitly enabled, the API origin must be the exact staging
 * host, and the signed-in Clerk email must be in the staging beta cohort.
 * Completing onboarding still uses the normal Clerk-authenticated API.
 */
export function canSkipZoomOnboarding(
  email: string | null | undefined,
): boolean {
  return (
    import.meta.env.VITE_STAGING_PILOT_ZOOM_SKIP === "true" &&
    pointsAtStagingApi(import.meta.env.VITE_PYTHON_URL) &&
    isBetaUserEnabled(email)
  )
}
