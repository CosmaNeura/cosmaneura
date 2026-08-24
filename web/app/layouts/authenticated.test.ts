import { describe, expect, it } from "vitest"

import {
  billingRecoveryLocation,
  needsBillingRecovery,
  needsOnboarding,
  shouldRedirectToBillingRecovery,
  shouldRetrySettingsQuery,
} from "./authenticated"

describe("authenticated access routing", () => {
  it("lets an effectively ready grandfathered account reach the dashboard", () => {
    expect(
      needsOnboarding({
        onboarding_completed: true,
        practice_ready: true,
        is_admin: false,
      }),
    ).toBe(false)
  })

  it("keeps an account on onboarding until server access is ready", () => {
    expect(
      needsOnboarding({
        onboarding_completed: true,
        practice_ready: false,
        is_admin: false,
      }),
    ).toBe(true)
  })

  it("routes an authorized account with lapsed entitlement to billing recovery", () => {
    expect(
      needsBillingRecovery({
        billing_recovery_required: true,
        practice_ready: true,
        is_admin: false,
      }),
    ).toBe(true)
    expect(
      billingRecoveryLocation(
        "https://app.cosmaneura.com/notes/note_123?tab=soap#assessment",
      ),
    ).toBe(
      "/settings?billing=required&returnTo=%2Fnotes%2Fnote_123%3Ftab%3Dsoap%23assessment",
    )
  })

  it("does not let an administrative capability bypass billing recovery", () => {
    expect(
      needsBillingRecovery({
        billing_recovery_required: true,
        practice_ready: true,
        is_admin: true,
      }),
    ).toBe(true)
  })

  it("does not route incomplete onboarding into billing recovery", () => {
    expect(
      needsBillingRecovery({
        billing_recovery_required: true,
        practice_ready: false,
        is_admin: false,
      }),
    ).toBe(false)
  })

  it("keeps the settings route reachable during billing recovery", () => {
    expect(
      shouldRedirectToBillingRecovery(
        {
          billing_recovery_required: true,
          practice_ready: true,
          is_admin: false,
        },
        "https://app.cosmaneura.com/settings?billing=success",
      ),
    ).toBe(false)
  })

  it("does not retry a durable account-recovery conflict", () => {
    expect(
      shouldRetrySettingsQuery(0, {
        detail:
          "This email is already linked to a previous sign-in. Contact support to recover the existing account; no records have been lost.",
      }),
    ).toBe(false)
    expect(shouldRetrySettingsQuery(0, new Error("transient"))).toBe(true)
    expect(shouldRetrySettingsQuery(3, new Error("transient"))).toBe(false)
  })
})
