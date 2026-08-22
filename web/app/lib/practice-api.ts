import { getClient } from "~/configuration/clerk"
import { requireEnv } from "~/utils/environment"

export type PracticeStatus = {
  legal_name: string
  join_code: string | null
  email_domain: string
  origin_channel: string
  pastoral_pack: boolean
  baa_accepted: boolean
  role: string
  attested: boolean
  access_authorized: boolean
  authorization_basis: string | null
  access_revoked: boolean
  trial_ends_at: string | null
  billing_exempt: boolean
  billing_exempt_until: string | null
  billing_exempt_active: boolean
  billing_enabled: boolean
  billing_management_enabled: boolean
  billing_portal_available: boolean
  billing_checkout_resumable: boolean
  billing_subscription_manageable: boolean
  billing_checkout_available_at: string | null
  entitled: boolean
  subscription_status: string | null
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

export type OrganizationAccessOpportunity = {
  organization_name: string
  email_domain: string
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

export function getOrganizationAccessOpportunity() {
  return practiceRequest<OrganizationAccessOpportunity | null>(
    "/practice/organization-access",
  )
}

export function claimOrganizationAccess(accessCode: string) {
  return practiceRequest<PracticeStatus>(
    "/practice/organization-access/claim",
    {
      method: "POST",
      body: JSON.stringify({ access_code: accessCode }),
    },
  )
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
  return Boolean(
    practice?.baa_accepted &&
      practice.access_authorized &&
      !practice.access_revoked,
  )
}

export type BaaDocument = {
  doc_id: string
  version: string
  sha256: string
  html: string
}

export type BaaAcceptanceRecord = {
  doc_id: string
  version: string
  accepted_by_name: string
  accepted_by_title: string
  accepted_by_email: string
  accepted_at_utc: string
  content_sha256: string
}

export function getBaaCurrent() {
  return practiceRequest<BaaDocument>("/baa/current")
}

export function getBaaAcceptances() {
  return practiceRequest<BaaAcceptanceRecord[]>("/practice/baa/acceptances")
}

// Both billing endpoints return a Stripe-hosted URL to send the browser to.
// They stay reachable after the trial lapses (the 402 gate exempts them).
export function createBillingCheckout() {
  return practiceRequest<{ url: string }>("/practice/billing/checkout", {
    method: "POST",
    body: JSON.stringify({
      success_path: "/settings?billing=success",
      cancel_path: "/settings?billing=cancelled",
    }),
  })
}

export function createBillingPortal() {
  return practiceRequest<{ url: string }>("/practice/billing/portal", {
    method: "POST",
    body: JSON.stringify({ return_path: "/settings" }),
  })
}
