// The token arrives in the URL fragment, which never reaches a server log or
// a Referer header, and survives the sign-in round trip in this tab only.
export const PENDING_INVITATION_KEY = "cosmas.pendingInvitation"

function storage(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

/** Move a `#t=` token into session storage and strip it from the address bar. */
export function capturePendingInvitation(): string | null {
  const token = new URLSearchParams(window.location.hash.slice(1)).get("t")
  if (token) {
    storage()?.setItem(PENDING_INVITATION_KEY, token)
    window.history.replaceState(
      window.history.state,
      "",
      window.location.pathname + window.location.search,
    )
  }
  return pendingInvitation()
}

export function pendingInvitation(): string | null {
  return storage()?.getItem(PENDING_INVITATION_KEY) ?? null
}

export function clearPendingInvitation() {
  storage()?.removeItem(PENDING_INVITATION_KEY)
}
