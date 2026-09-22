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

export const APP_LOADING_DELAY_COPY =
  "The application is taking longer than usual to load."

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
        "Choose a sign-in method shown below, such as Google or an email code. For email codes, use the newest message. If it has not arrived, check spam or request another code when available.",
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
