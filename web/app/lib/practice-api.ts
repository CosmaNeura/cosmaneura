import { getSessionToken } from "~/configuration/clerk"
import { internalApiFetch, safeBillingReturnTo } from "~/lib/billing-recovery"
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
  pilot_access_grant_active: boolean
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
  state: "ready" | "claim" | "pending" | "support"
  organization_name: string | null
  email_domain: string
}

export type PublicOnboardingStep =
  | "account"
  | "practice"
  | "baa"
  | "legal"
  | "checkout"
  | "credential"

export type PublicOnboardingCurrentStep =
  | Exclude<PublicOnboardingStep, "account">
  | "complete"

export type PublicSubscriptionState =
  | "none"
  | "incomplete"
  | "incomplete_expired"
  | "pending_verification"
  | "invalid"
  | "trialing"
  | "active"
  | "past_due"
  | "unpaid"
  | "paused"
  | "canceled"

export type CredentialState =
  | "pending"
  | "approved"
  | "rejected"
  | "needs_review"

export type PublicOnboardingState = {
  contract_version: "public-onboarding.v1"
  managed_path: boolean
  completed_steps: PublicOnboardingStep[]
  current_step: PublicOnboardingCurrentStep
  practice: {
    legal_name: string
    role: "owner" | "workforce"
  } | null
  baa_accepted: boolean
  terms_accepted: boolean
  privacy_accepted: boolean
  checkout_started: boolean
  subscription_state: PublicSubscriptionState
  trial_ends_at: string | null
  credential_required: boolean
  credential_state: CredentialState
  phi_access_allowed: boolean
}

export type PublicLegalDocument = {
  kind: "terms" | "privacy"
  version: string
  sha256: string
  path: string
}

export type PublicLegalDocuments = {
  contract_version: "public-legal-documents.v1"
  documents: PublicLegalDocument[]
}

export type PublicPracticeInvitation = {
  invitation_id: string
  invitee_email: string
  status: "pending" | "accepted" | "revoked" | "expired"
  role: "workforce"
  issued_at: string
  expires_at: string
  invite_token: string | null
  reused_idempotency_key: boolean
}

export type PublicPracticeMember = {
  member_id: string
  email: string | null
  role: "owner" | "workforce"
  seat_state: "active" | "inactive"
  credential_state: CredentialState
  access_revoked: boolean
}

export type CapabilityDecision = {
  decision: "allowed" | "denied"
  data_mode: "none" | "synthetic_only" | "real_phi"
  rollout: "disabled" | "cohort" | "enabled"
  operation_mode: "standard" | "read_only" | "web_pwa_first" | "advisory_only"
  reason_codes: Array<
    | "allowed"
    | "email_unverified"
    | "membership_inactive"
    | "membership_revoked"
    | "legal_incomplete"
    | "subscription_inactive"
    | "credential_pending"
    | "credential_needs_review"
    | "credential_rejected"
    | "cohort_ineligible"
    | "rollout_disabled"
    | "policy_unavailable"
  >
}

export type PublicCapabilityEntitlement = {
  contract_version: "public-capability-entitlements.v1"
  policy_revision: "2026-08-27"
  generated_at: string
  actor: {
    actor_id: string
    practice_id: string
    role: "owner" | "workforce"
    membership_state: "active" | "inactive" | "revoked"
  }
  gates: {
    email_verified: boolean
    baa_accepted: boolean
    terms_accepted: boolean
    privacy_accepted: boolean
    subscription_state: PublicSubscriptionState
    credential_required: boolean
    credential_state: CredentialState
  }
  seats: {
    active_clinician_seats: number
    maximum_active_clinician_seats: 20
    pending_invitations: number
    stripe_quantity_sync_required: boolean
  }
  capabilities: {
    soap_note: CapabilityDecision
    intake_prep: CapabilityDecision
    session_prep: CapabilityDecision
    scribe: CapabilityDecision
    cab: CapabilityDecision
  }
}

async function authHeaders(): Promise<HeadersInit> {
  const token = await getSessionToken()
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
  const res = await internalApiFetch(`${apiBase()}/internal/v1${path}`, {
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

export function getPublicOnboarding() {
  return practiceRequest<PublicOnboardingState>("/public-onboarding")
}

export function getCurrentLegalDocuments() {
  return practiceRequest<PublicLegalDocuments>("/legal/current")
}

export function acceptPracticeLegal(body: {
  signature_name: string
  signature_title: string
  authority_confirmed: true
  documents: Array<Pick<PublicLegalDocument, "kind" | "version" | "sha256">>
}) {
  return practiceRequest<PublicOnboardingState>("/practice/legal", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function getPublicCapabilities() {
  return practiceRequest<PublicCapabilityEntitlement>("/capabilities")
}

export function getPublicPracticeInvitations() {
  return practiceRequest<PublicPracticeInvitation[]>("/practice/invitations")
}

export function createPublicPracticeInvitation(
  body: { email: string; expires_in_days: number },
  idempotencyKey: string,
) {
  return practiceRequest<PublicPracticeInvitation>("/practice/invitations", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(body),
  })
}

export function revokePublicPracticeInvitation(
  invitationId: string,
  reason: string,
) {
  return practiceRequest<PublicPracticeInvitation>(
    `/practice/invitations/${encodeURIComponent(invitationId)}/revoke`,
    {
      method: "POST",
      body: JSON.stringify({ reason }),
    },
  )
}

export function acceptPublicPracticeInvitation(body: {
  token: string
  attestation_name: string
  checks: string[]
}) {
  return practiceRequest<PublicPracticeMember>("/practice/invitations/accept", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function getPublicPracticeMembers() {
  return practiceRequest<PublicPracticeMember[]>("/practice/members")
}

export function deactivatePublicPracticeMember(memberId: string) {
  return practiceRequest<PublicPracticeMember>(
    `/practice/members/${encodeURIComponent(memberId)}/deactivate`,
    { method: "POST" },
  )
}

export function reactivatePublicPracticeMember(memberId: string) {
  return practiceRequest<PublicPracticeMember>(
    `/practice/members/${encodeURIComponent(memberId)}/reactivate`,
    { method: "POST" },
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
export function billingCheckoutPaths(returnTo?: string | null) {
  const buildPaths = (safeReturnTo: string) => {
    const successParams = new URLSearchParams({
      billing: "success",
      returnTo: safeReturnTo,
    })
    const cancelParams = new URLSearchParams({
      billing: "cancelled",
      returnTo: safeReturnTo,
    })
    return {
      successPath: `/settings?${successParams.toString()}`,
      cancelPath: `/settings?${cancelParams.toString()}`,
    }
  }

  const paths = buildPaths(safeBillingReturnTo(returnTo))
  // The backend intentionally caps Stripe redirect paths at 200 characters.
  // Preserve normal deep links, but never turn an unusually long current URL
  // into a failed Checkout request.
  if (paths.successPath.length <= 200 && paths.cancelPath.length <= 200) {
    return paths
  }
  return buildPaths("/notes")
}

export function createBillingCheckout(returnTo?: string | null) {
  const paths = billingCheckoutPaths(returnTo)
  return practiceRequest<{ url: string }>("/practice/billing/checkout", {
    method: "POST",
    body: JSON.stringify({
      success_path: paths.successPath,
      cancel_path: paths.cancelPath,
    }),
  })
}

export function createBillingPortal() {
  return practiceRequest<{ url: string }>("/practice/billing/portal", {
    method: "POST",
    body: JSON.stringify({ return_path: "/settings" }),
  })
}
