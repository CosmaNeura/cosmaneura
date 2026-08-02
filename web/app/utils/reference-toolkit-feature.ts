// Per-environment allowlist for the provider reference toolkit (medication
// registry + resource panel, and the interaction/lab tools that build on it).
//
// This deliberately does NOT use the shared beta allowlist in beta-access.ts.
// That list already carries a production cohort for the Zoom recording bot and
// the engagement badge, so borrowing it here would render the toolkit UI for
// production users while the server refuses every request — the feature would
// look broken rather than hidden.
//
// Mirrors app/services/reference_toolkit_gate.py, including its three-way
// environment split: staging has an explicit cohort, production is empty until
// the staging pilot validates the feature, and local dev is ungated. Keep the
// two lists in sync.
//
// Staging is detected from VITE_PYTHON_URL, NOT import.meta.env.MODE: deployed
// builds run `react-router build` with no --mode, so MODE is always
// "production" and a MODE check would never see staging.

const STAGING_REFERENCE_TOOLKIT_EMAILS = new Set([
  "john@cosmaneura.com",
  "dev@cosmaneura.com",
  "greg@mycatholicdoctor.com",
  "kathleen@mycatholicdoctor.com",
])

// Empty until the staging pilot validates the feature.
const PROD_REFERENCE_TOOLKIT_EMAILS = new Set<string>([])

const STAGING_HOST = "staging-app.cosmaneura.com"
const PROD_HOST = "app.cosmaneura.com"

function pythonUrl(): string {
  return import.meta.env.VITE_PYTHON_URL ?? ""
}

function isStagingDeployment(): boolean {
  return pythonUrl().includes(STAGING_HOST)
}

function isProductionDeployment(): boolean {
  // STAGING_HOST contains PROD_HOST as a substring, so staging must lose first
  return !isStagingDeployment() && pythonUrl().includes(PROD_HOST)
}

export function isReferenceToolkitEnabled(
  email: string | null | undefined,
): boolean {
  if (!email) return false

  const normalized = email.toLowerCase()

  if (isStagingDeployment()) {
    return STAGING_REFERENCE_TOOLKIT_EMAILS.has(normalized)
  }

  if (isProductionDeployment()) {
    return PROD_REFERENCE_TOOLKIT_EMAILS.has(normalized)
  }

  // local dev / test: ungated, matching the server
  return true
}
