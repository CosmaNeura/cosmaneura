// Staging exercises Zoom only through a dedicated internal test account, and
// that same account must NOT run in production: if it were connected in both
// (separate Zoom apps), one real meeting would get two bots. So the rule is
// opposite per environment:
//   staging     -> only the test account is enabled
//   production  -> everyone EXCEPT the test account is enabled
// import.meta.env.MODE is unreliable (always "production" in deployed builds, so
// isStaging() is never true), so staging is detected from VITE_PYTHON_URL. The
// server mirrors this in app/services/zoom_webhooks.py.
const ZOOM_TEST_ACCOUNTS = new Set(["zoom-reviewer@cosmaneura.com"])

function isStagingDeployment(): boolean {
  return (import.meta.env.VITE_PYTHON_URL ?? "").includes(
    "staging-app.cosmaneura.com",
  )
}

export function isZoomEnabled(email: string | null | undefined): boolean {
  if (!email) return false
  const isTestAccount = ZOOM_TEST_ACCOUNTS.has(email.toLowerCase())
  return isStagingDeployment() ? isTestAccount : !isTestAccount
}
