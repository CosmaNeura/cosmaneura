// Zoom is gated by an explicit per-environment allowlist:
//   - staging:    only the dedicated internal test account
//   - production: only the specific beta accounts below
// Staging is detected from VITE_PYTHON_URL, NOT import.meta.env.MODE: deployed
// builds run `react-router build` with no --mode, so MODE is always
// "production" and isStaging() would never be true. The server mirrors these
// allowlists in app/services/zoom_webhooks.py.
const STAGING_ZOOM_EMAILS = new Set(["zoom-reviewer@cosmaneura.com"])

const PROD_ZOOM_EMAILS = new Set([
  "dev@cosmaneura.com",
  "greg@mycatholicdoctor.com",
  "kathleen@mycatholicdoctor.com",
  "john@cosmaneura.com",
  "ortegaoscar14@gmail.com",
])

function isStagingDeployment(): boolean {
  return (import.meta.env.VITE_PYTHON_URL ?? "").includes(
    "staging-app.cosmaneura.com",
  )
}

export function isZoomEnabled(email: string | null | undefined): boolean {
  if (!email) return false
  const allowlist = isStagingDeployment() ? STAGING_ZOOM_EMAILS : PROD_ZOOM_EMAILS
  return allowlist.has(email.toLowerCase())
}
