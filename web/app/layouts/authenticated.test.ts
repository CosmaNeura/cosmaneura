import { describe, expect, it } from "vitest"

import { needsOnboarding } from "./authenticated"

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
})
