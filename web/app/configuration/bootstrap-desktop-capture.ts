const CONNECT_PATH = "/desktop-capture/connect"
const STORAGE_KEY = "cosmas.desktop-capture.handoff.v1"

function sanitizeConnectFragment() {
  if (
    typeof window === "undefined" ||
    window.location.pathname !== CONNECT_PATH ||
    window.location.hash.length <= 1
  ) {
    return
  }

  const fragment = window.location.hash.slice(1)
  try {
    window.sessionStorage.setItem(STORAGE_KEY, fragment)
  } catch {
    // Storage can be unavailable in hardened/private browser contexts. The
    // route will fail closed after the fragment is removed.
  } finally {
    // Authorization material must be gone before telemetry providers inspect
    // the current URL. A storage failure therefore still clears the fragment
    // and produces a safe, recoverable handoff failure.
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${window.location.search}`,
    )
  }
}

sanitizeConnectFragment()

export function takeDesktopCaptureHandoff(): string | null {
  if (typeof window === "undefined") return null
  try {
    const fragment = window.sessionStorage.getItem(STORAGE_KEY)
    window.sessionStorage.removeItem(STORAGE_KEY)
    return fragment
  } catch {
    return null
  }
}
