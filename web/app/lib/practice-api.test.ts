import { describe, expect, it } from "vitest"

import { type PracticeStatus, practiceReady } from "./practice-api"

function practice(overrides: Partial<PracticeStatus> = {}): PracticeStatus {
  return {
    legal_name: "Pilot Organization",
    join_code: "PILOT123",
    email_domain: "example.com",
    origin_channel: "direct",
    pastoral_pack: false,
    baa_accepted: true,
    role: "workforce",
    attested: false,
    access_authorized: true,
    authorization_basis: "organization_roster",
    access_revoked: false,
    trial_ends_at: null,
    billing_exempt: true,
    billing_exempt_until: null,
    billing_exempt_active: true,
    billing_enabled: false,
    billing_management_enabled: false,
    billing_portal_available: false,
    billing_checkout_resumable: false,
    billing_subscription_manageable: false,
    billing_checkout_available_at: null,
    entitled: true,
    subscription_status: null,
    ...overrides,
  }
}

describe("practiceReady", () => {
  it("accepts organization authorization without a personal attestation", () => {
    expect(practiceReady(practice())).toBe(true)
  })

  it("still requires a BAA in force", () => {
    expect(practiceReady(practice({ baa_accepted: false }))).toBe(false)
  })

  it("fails closed after organization access is revoked", () => {
    expect(practiceReady(practice({ access_revoked: true }))).toBe(false)
  })
})
