import { describe, expect, it } from "vitest"

import {
  type PracticeStatus,
  billingCheckoutPaths,
  practiceReady,
} from "./practice-api"

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
    pilot_access_grant_active: false,
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

describe("billingCheckoutPaths", () => {
  it("carries the validated recovery destination through success and cancel", () => {
    expect(billingCheckoutPaths("/schedule?view=week#today")).toEqual({
      successPath:
        "/settings?billing=success&returnTo=%2Fschedule%3Fview%3Dweek%23today",
      cancelPath:
        "/settings?billing=cancelled&returnTo=%2Fschedule%3Fview%3Dweek%23today",
    })
  })

  it("falls back to notes for an off-site return destination", () => {
    expect(billingCheckoutPaths("https://evil.example")).toEqual({
      successPath: "/settings?billing=success&returnTo=%2Fnotes",
      cancelPath: "/settings?billing=cancelled&returnTo=%2Fnotes",
    })
  })

  it("falls back to notes when an encoded deep link exceeds the API limit", () => {
    expect(billingCheckoutPaths(`/notes?search=${"x".repeat(300)}`)).toEqual({
      successPath: "/settings?billing=success&returnTo=%2Fnotes",
      cancelPath: "/settings?billing=cancelled&returnTo=%2Fnotes",
    })
  })
})
