// The token arrives in the URL fragment, which never reaches a server log or
// a Referer header, and survives the sign-in round trip in this tab only.
export const PENDING_INVITATION_KEY = "cosmas.pendingInvitation"

// Where storage is unavailable the link still works for this page load.
let memoryToken: string | null = null

function read(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string) {
  try {
    window.sessionStorage.setItem(key, value)
  } catch {
    // Quota or privacy mode: the in-memory copy still serves this page load.
  }
}

function remove(key: string) {
  try {
    window.sessionStorage.removeItem(key)
  } catch {
    // Nothing was stored, so there is nothing to remove.
  }
}

/** Move a `#t=` token into session storage and strip it from the address bar. */
export function capturePendingInvitation(): string | null {
  const token = new URLSearchParams(window.location.hash.slice(1)).get("t")
  if (token) {
    memoryToken = token
    write(PENDING_INVITATION_KEY, token)
    window.history.replaceState(
      window.history.state,
      "",
      window.location.pathname + window.location.search,
    )
  }
  return pendingInvitation()
}

export function pendingInvitation(): string | null {
  return read(PENDING_INVITATION_KEY) ?? memoryToken
}

export function clearPendingInvitation() {
  memoryToken = null
  remove(PENDING_INVITATION_KEY)
}
