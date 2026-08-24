import { describe, expect, it } from "vitest"

import {
  billingRecoveryLocation,
  needsBillingRecovery,
  needsOnboarding,
  shouldRedirectToBillingRecovery,
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

  it("keeps a non-admin account on onboarding until server access is ready", () => {
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
        "https://app.cosmaneura.com/notes/note_123?tab=soap",
      ),
    ).toBe(
      "/settings?billing=required&returnTo=%2Fnotes%2Fnote_123%3Ftab%3Dsoap",
    )
  })

  it("does not route admins or incomplete onboarding into billing recovery", () => {
    expect(
      needsBillingRecovery({
        billing_recovery_required: true,
        practice_ready: true,
        is_admin: true,
      }),
    ).toBe(false)
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
})
