import { href } from "react-router"

export const PUBLIC_INVITATION_TOKEN_PATTERN = /^[A-Za-z0-9_-]{32,128}$/u
const PUBLIC_INVITATION_SESSION_KEY = "cosmas.public-practice-invitation.v1"

export type PublicInvitationLink = {
  token: string | null
  invalid: boolean
}

export function consumePublicInvitationLink(): PublicInvitationLink {
  if (typeof window === "undefined") {
    return { token: null, invalid: false }
  }
  let raw: string | null = null
  try {
    raw = window.sessionStorage.getItem(PUBLIC_INVITATION_SESSION_KEY)
  } catch {
    // Storage may be unavailable in hardened browsers. A signed-in visitor can
    // still consume the fragment during this loader pass.
  }
  if (window.location.hash) {
    const fragmentToken = new URLSearchParams(
      window.location.hash.slice(1),
    ).get("registrationToken")
    if (fragmentToken !== null) {
      raw = fragmentToken
      const normalized = fragmentToken.trim()
      try {
        if (PUBLIC_INVITATION_TOKEN_PATTERN.test(normalized)) {
          window.sessionStorage.setItem(
            PUBLIC_INVITATION_SESSION_KEY,
            normalized,
          )
        } else {
          window.sessionStorage.removeItem(PUBLIC_INVITATION_SESSION_KEY)
        }
      } catch {
        // See the storage fallback above.
      }
      // Fragments do not reach the server. Clear this one before rendering so
      // browser history and replay tools do not retain bearer material.
      window.history.replaceState(
        window.history.state,
        "",
        `${window.location.pathname}${window.location.search}`,
      )
    }
  }
  if (raw === null) return { token: null, invalid: false }

  const token = raw.trim()
  return PUBLIC_INVITATION_TOKEN_PATTERN.test(token)
    ? { token, invalid: false }
    : { token: null, invalid: true }
}

export function clearConsumedPublicInvitationLink() {
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.removeItem(PUBLIC_INVITATION_SESSION_KEY)
  } catch {
    // Nothing else should retain the token when storage is unavailable.
  }
}

export function finishPublicInvitationFlow(
  navigate: () => void = () => window.location.replace(href("/onboarding")),
) {
  clearConsumedPublicInvitationLink()
  navigate()
}
