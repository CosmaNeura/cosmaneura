// Shared per-environment beta allowlist for features still gated to specific
// accounts (the Zoom recording bot and the engagement-role "personality"
// badge both reference this single list, so the cohorts can never drift apart).
//
// Staging and production have separate allowlists. Staging is detected from
// VITE_PYTHON_URL, NOT import.meta.env.MODE: deployed builds run
// `react-router build` with no --mode, so MODE is always "production" and
// isStaging() would never be true. The server mirrors the production list in
// app/services/zoom_webhooks.py.
const STAGING_BETA_EMAILS = new Set(["zoom-reviewer@cosmaneura.com"])

const PROD_BETA_EMAILS = new Set([
  "dev@cosmaneura.com",
  "greg@mycatholicdoctor.com",
  "kathleen@mycatholicdoctor.com",
  "john@cosmaneura.com",
  "ortegaoscar14@gmail.com",
  // Pilot cohort
  "sweeman@fiathealth.org",
  "christa.thornberry@mycatholicdoctor.com",
  "maura.buete@mycatholicdoctor.com",
  "sherin.george@mycatholicdoctor.com",
])

function isStagingDeployment(): boolean {
  return (import.meta.env.VITE_PYTHON_URL ?? "").includes(
    "staging-app.cosmaneura.com",
  )
}

export function isBetaUserEnabled(email: string | null | undefined): boolean {
  if (!email) return false
  const allowlist = isStagingDeployment()
    ? STAGING_BETA_EMAILS
    : PROD_BETA_EMAILS
  return allowlist.has(email.toLowerCase())
}
