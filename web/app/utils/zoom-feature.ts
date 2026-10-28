// Preserve the existing beta cohort. The two synthetic lab accounts are a
// Zoom-only staging addition; they do not receive unrelated beta features.
import { isBetaUserEnabled } from "./beta-access"

const STAGING_ZOOM_LAB_EMAILS = new Set([
  "scribe-lab-a+clerk_test@cosmaneura.com",
  "scribe-lab-b+clerk_test@cosmaneura.com",
])

function isExactStagingOrigin(): boolean {
  try {
    const url = new URL(import.meta.env.VITE_PYTHON_URL ?? "")
    return (
      url.origin === "https://staging-app.cosmaneura.com" &&
      url.username === "" &&
      url.password === "" &&
      url.pathname === "/" &&
      url.search === "" &&
      url.hash === ""
    )
  } catch {
    return false
  }
}

export function isZoomEnabled(email: string | null | undefined): boolean {
  if (STAGING_ZOOM_LAB_EMAILS.has((email ?? "").trim().toLowerCase())) {
    return isExactStagingOrigin()
  }
  return isBetaUserEnabled(email)
}
