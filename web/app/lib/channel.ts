export const CHANNELS = ["direct", "catholic", "mcd"] as const
export type Channel = (typeof CHANNELS)[number]

const STORAGE_KEY = "cosmas.channel"

export function parseChannel(value: unknown): Channel {
  return CHANNELS.includes(value as Channel) ? (value as Channel) : "direct"
}

export function readStoredChannel(): Channel {
  if (typeof window === "undefined") return "direct"
  try {
    return parseChannel(sessionStorage.getItem(STORAGE_KEY))
  } catch {
    return "direct"
  }
}

export function storeChannel(channel: Channel) {
  if (typeof window === "undefined") return
  try {
    sessionStorage.setItem(STORAGE_KEY, channel)
  } catch {
    /* ignore quota / private mode */
  }
}

/** Search param wins and is persisted. Otherwise the session value. */
export function resolveChannel(fromSearch?: string | null): Channel {
  if (fromSearch && CHANNELS.includes(fromSearch as Channel)) {
    const channel = fromSearch as Channel
    storeChannel(channel)
    return channel
  }
  return readStoredChannel()
}

export const SIGN_IN_LOAD_ERROR_COPY = "sign-in didn’t load — refresh"

export type LoginCopy = {
  kicker: string
  title: string
  body: string
  methodHint?: string
}

export function loginCopy(channel: Channel): LoginCopy {
  if (channel === "mcd") {
    return {
      kicker: "MyCatholicDoctor",
      title: "Continue in Cosmas — MyCatholicDoctor",
      body: "Sign in with the @mycatholicdoctor.com address MCD has on file. Same Cosmas. Same Cosmaneura Inc BAA on the practice.",
      methodHint:
        "Use Google or one email code (use the newest mail). Do not tap resend. There is no password for this group — the password box is a dead end.",
    }
  }
  if (channel === "catholic") {
    return {
      kicker: "Catholic practices",
      title: "Continue in Cosmas.",
      body: "Built for how Catholic physicians practice. Use your practice work email. Authentication first, then the Cosmaneura Inc BAA.",
    }
  }
  return {
    kicker: "Independent clinics",
    title: "Continue in Cosmas.",
    body: "Use your practice work email. Authentication first. Then the Cosmaneura Inc BAA. Patient information stays locked until both are done.",
  }
}
