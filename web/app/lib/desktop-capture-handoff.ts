const OPAQUE_SECRET = /^[A-Za-z0-9_-]+$/
const CODE_CHALLENGE = /^[A-Za-z0-9_-]{43}$/
const REDIRECT_URI = "cosmaneura-capture://oauth/callback"

const HANDOFF_KEYS = [
  "launch_ticket",
  "code_challenge",
  "code_challenge_method",
  "redirect_uri",
  "installation_thumbprint",
  "state",
] as const

export type DesktopCaptureHandoff = {
  launchTicket: string
  codeChallenge: string
  redirectUri: typeof REDIRECT_URI
  installationThumbprint: string
  state: string
}

function isOpaqueSecret(value: string, minimum = 43) {
  return (
    value.length >= minimum && value.length <= 128 && OPAQUE_SECRET.test(value)
  )
}

export function parseDesktopCaptureHandoff(
  fragment: string | null,
): DesktopCaptureHandoff | null {
  if (!fragment) return null
  const normalized = fragment.startsWith("#") ? fragment.slice(1) : fragment
  const params = new URLSearchParams(normalized)
  const entries = [...params.entries()]
  if (
    entries.length !== HANDOFF_KEYS.length ||
    !HANDOFF_KEYS.every((key) => params.getAll(key).length === 1) ||
    entries.some(
      ([key]) => !HANDOFF_KEYS.includes(key as (typeof HANDOFF_KEYS)[number]),
    )
  ) {
    return null
  }

  const launchTicket = params.get("launch_ticket") ?? ""
  const codeChallenge = params.get("code_challenge") ?? ""
  const redirectUri = params.get("redirect_uri") ?? ""
  const installationThumbprint = params.get("installation_thumbprint") ?? ""
  const state = params.get("state") ?? ""
  if (
    !isOpaqueSecret(launchTicket) ||
    !CODE_CHALLENGE.test(codeChallenge) ||
    params.get("code_challenge_method") !== "S256" ||
    redirectUri !== REDIRECT_URI ||
    !CODE_CHALLENGE.test(installationThumbprint) ||
    !isOpaqueSecret(state)
  ) {
    return null
  }

  return {
    launchTicket,
    codeChallenge,
    redirectUri: REDIRECT_URI,
    installationThumbprint,
    state,
  }
}

export function buildDesktopAuthorizationCallback(
  authorizationCode: string,
  returnedRedirectUri: string,
  state: string,
): string | null {
  if (
    returnedRedirectUri !== REDIRECT_URI ||
    !isOpaqueSecret(authorizationCode) ||
    !isOpaqueSecret(state)
  ) {
    return null
  }
  const callback = new URL(REDIRECT_URI)
  callback.searchParams.set("code", authorizationCode)
  callback.searchParams.set("state", state)
  return callback.toString()
}

export function parseParticipantRegions(value: string): string[] | null {
  const regions = [
    ...new Set(
      value
        .split(",")
        .map((region) => region.trim().toUpperCase())
        .filter(Boolean)
        .map((region) => (region.length === 2 ? `US-${region}` : region)),
    ),
  ]
  if (
    regions.length === 0 ||
    regions.length > 8 ||
    regions.some((region) => !/^US-[A-Z]{2}$/.test(region))
  ) {
    return null
  }
  return regions
}

export function buildDesktopLaunchUrl(launchTicket: string): string | null {
  if (!isOpaqueSecret(launchTicket)) return null
  const launch = new URL("cosmaneura-capture://launch")
  launch.searchParams.set("launch_ticket", launchTicket)
  return launch.toString()
}
