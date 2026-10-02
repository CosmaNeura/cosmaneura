/** Capture before account SDKs or telemetry; never use browser persistence. */
export function isVisitPrepPatientPath(pathname: string): boolean {
  return pathname === "/visit-prep" || pathname.startsWith("/visit-prep/")
}

export function isVisitPrepPatientBrowser(): boolean {
  return (
    typeof window !== "undefined" &&
    isVisitPrepPatientPath(window.location.pathname)
  )
}

export function isCanonicalVisitPrepToken(value: string): boolean {
  // The last base64url sextet has two unused bits for a 256-bit credential.
  return /^vp1_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/.test(value)
}

type Browser = Pick<Window, "location" | "history" | "addEventListener">
export const VISIT_PREP_CREDENTIAL_CHANGE_EVENT =
  "cosmas:visit-prep-credential-change"
let credential: string | null = null
let continuationAllowed = false

export function captureVisitPrepCredential(browser: Browser): void {
  if (!isVisitPrepPatientPath(browser.location.pathname)) return
  const { pathname, search, hash } = browser.location
  // Remove even malformed links before any instrumentation observes history.
  // Reject search parameters rather than accepting credentials from them.
  browser.history.replaceState(browser.history.state, "", pathname)
  credential = null
  continuationAllowed = pathname === "/visit-prep" && !search && !hash
  if (pathname !== "/visit-prep" || search) return
  const match = /^#access_token=(vp1_[A-Za-z0-9_-]{43})$/.exec(hash)
  if (match && isCanonicalVisitPrepToken(match[1])) {
    credential = match[1]
    continuationAllowed = true
  }
}

export function readVisitPrepCredential(): string | null {
  return credential
}

export function canResumeVisitPrepConfirmation(): boolean {
  return continuationAllowed
}

export function forgetVisitPrepCredential(): void {
  credential = null
  continuationAllowed = false
}

if (typeof window !== "undefined") {
  captureVisitPrepCredential(window)
  window.addEventListener("pagehide", forgetVisitPrepCredential)
  window.addEventListener("hashchange", () => {
    // An initial capture can leave a queued hashchange whose fragment was
    // already removed. Only a newly supplied fragment starts another flow.
    if (!isVisitPrepPatientBrowser() || !window.location.hash) return
    captureVisitPrepCredential(window)
    window.dispatchEvent(new Event(VISIT_PREP_CREDENTIAL_CHANGE_EVENT))
  })
}
