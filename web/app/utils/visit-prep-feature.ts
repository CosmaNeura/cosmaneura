// Per-environment allowlist for Visit Prep (the physician-authored pre-visit
// interview that replaces the turned-off intake chatbot — CosmaNeura#135).
//
// This deliberately does NOT use the shared beta allowlist in beta-access.ts.
// That list already carries a production cohort for the Zoom recording bot and
// the engagement badge — including the MyCatholicDoctor accounts that are
// Visit Prep's eventual launch cohort. Borrowing it here would surface a
// half-built Visit Prep tab to those users from the very first merge, which is
// exactly the failure mode reference-toolkit-feature.ts was written to avoid.
//
// Mirrors app/services/reference_toolkit_gate.py's three-way environment split:
// staging has an explicit cohort, production is empty until the staging pilot
// validates the feature, and local dev is ungated.
//
// Staging is detected from VITE_PYTHON_URL, NOT import.meta.env.MODE: deployed
// builds run `react-router build` with no --mode, so MODE is always
// "production" and a MODE check would never see staging.

// Internal testers only while the feature is under construction. The
// MyCatholicDoctor staging accounts (greg@, kathleen@) are deliberately NOT
// here yet — they go in once the interview and brief actually work end to end,
// so the pilot's first look is at something finished.
const STAGING_VISIT_PREP_EMAILS = new Set([
  "john@cosmaneura.com",
  "dev@cosmaneura.com",
])

// Empty until the staging pilot validates the feature.
const PROD_VISIT_PREP_EMAILS = new Set<string>([])

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

export function isVisitPrepEnabled(email: string | null | undefined): boolean {
  if (!email) return false

  const normalized = email.toLowerCase()

  if (isStagingDeployment()) {
    return STAGING_VISIT_PREP_EMAILS.has(normalized)
  }

  if (isProductionDeployment()) {
    return PROD_VISIT_PREP_EMAILS.has(normalized)
  }

  // local dev / test: ungated
  return true
}
