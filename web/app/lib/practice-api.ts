import { getClient } from "~/configuration/clerk"
import { requireEnv } from "~/utils/environment"

export type PracticeStatus = {
  legal_name: string
  join_code: string
  email_domain: string
  origin_channel: string
  pastoral_pack: boolean
  baa_accepted: boolean
  role: string
  attested: boolean
}

export type JoinMismatch = {
  practice_name: string
  required_domain: string
  signed_in_domain: string | null
}

export type PracticeLookupResult = {
  practice: PracticeStatus | null
  mismatch: JoinMismatch | null
}

async function authHeaders(): Promise<HeadersInit> {
  const clerk = await getClient()
  const token = await clerk!.session!.getToken()
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  }
}

function apiBase() {
  return requireEnv("VITE_PYTHON_URL").replace(/\/$/, "")
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === "string") return body.detail
    if (Array.isArray(body.detail)) {
      return body.detail
        .map((item) => {
          if (typeof item === "string") return item
          if (
            item &&
            typeof item === "object" &&
            "msg" in item &&
            typeof item.msg === "string"
          ) {
            return item.msg
          }
          return JSON.stringify(item)
        })
        .join("; ")
    }
  } catch {
    /* ignore non-JSON */
  }
  return `Request failed (${res.status})`
}

async function practiceRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${apiBase()}/internal/v1${path}`, {
    ...init,
    headers: { ...(await authHeaders()), ...(init?.headers ?? {}) },
    credentials: "include",
  })
  if (!res.ok) throw new Error(await parseError(res))
  if (res.status === 204) return null as T
  const text = await res.text()
  if (!text) return null as T
  return JSON.parse(text) as T
}

export function getPractice() {
  return practiceRequest<PracticeStatus | null>("/practice")
}

export function createPractice(body: {
  legal_name: string
  npi?: string | null
  city?: string | null
  region?: string | null
  is_solo: boolean
  channel: string
  pastoral_pack: boolean
}) {
  return practiceRequest<PracticeStatus>("/practice", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function acceptPracticeBaa(body: {
  signature_name: string
  signature_title: string
  checks: string[]
}) {
  return practiceRequest<PracticeStatus>("/practice/baa", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function lookupPractice(join_code: string) {
  return practiceRequest<PracticeLookupResult>("/practice/lookup", {
    method: "POST",
    body: JSON.stringify({ join_code }),
  })
}

export function joinPractice(body: {
  join_code: string
  attestation_name: string
  checks: string[]
}) {
  return practiceRequest<PracticeStatus>("/practice/join", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function practiceReady(
  practice: PracticeStatus | null | undefined,
): practice is PracticeStatus {
  return Boolean(practice?.baa_accepted && practice.attested)
}
