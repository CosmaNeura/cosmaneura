// The token arrives in the URL fragment, which never reaches a server log or
// a Referer header, and survives the sign-in round trip in this tab only.
export const PENDING_INVITATION_KEY = "cosmas.pendingInvitation"
// Set to the stored token once /invite has shown it cannot be acted on yet,
// so onboarding stops sending the doctor back to a page with nothing to do.
export const PARKED_INVITATION_KEY = "cosmas.pendingInvitation.parked"

// Where storage is unavailable the link still works for this page load.
let memory: { token: string | null; parked: string | null } = {
  token: null,
  parked: null,
}
let keepThroughNextSignOut = false

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
    memory = { token, parked: null }
    write(PENDING_INVITATION_KEY, token)
    remove(PARKED_INVITATION_KEY)
    window.history.replaceState(
      window.history.state,
      "",
      window.location.pathname + window.location.search,
    )
  }
  return pendingInvitation()
}

export function pendingInvitation(): string | null {
  return read(PENDING_INVITATION_KEY) ?? memory.token
}

/** The stored token, unless /invite last found nothing the doctor can do. */
export function actionablePendingInvitation(): string | null {
  const token = pendingInvitation()
  const parked = read(PARKED_INVITATION_KEY) ?? memory.parked
  return token && token !== parked ? token : null
}

export function parkPendingInvitation(token: string) {
  memory = { ...memory, parked: token }
  write(PARKED_INVITATION_KEY, token)
}

export function unparkPendingInvitation() {
  memory = { ...memory, parked: null }
  remove(PARKED_INVITATION_KEY)
}

export function clearPendingInvitation() {
  memory = { token: null, parked: null }
  remove(PENDING_INVITATION_KEY)
  remove(PARKED_INVITATION_KEY)
}

/** Keep the token across one sign-out, to sign back in as its addressee. */
export function keepPendingInvitationThroughSignOut() {
  keepThroughNextSignOut = true
}

type SignInListener = (resources: { user?: unknown }) => void

/**
 * Forget the token when this tab signs out, so the next account to sign in
 * is not sent to someone else's invitation.
 */
export function forgetPendingInvitationOnSignOut(clerk: {
  addListener: (listener: SignInListener) => () => void
}): () => void {
  let signedIn: boolean | null = null
  return clerk.addListener(({ user }) => {
    const nowSignedIn = Boolean(user)
    if (signedIn && !nowSignedIn) {
      if (keepThroughNextSignOut) keepThroughNextSignOut = false
      else clearPendingInvitation()
    }
    signedIn = nowSignedIn
  })
}
