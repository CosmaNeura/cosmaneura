import { afterEach, describe, expect, it, vi } from "vitest"

import { isEngagementEnabled } from "./engagement-feature"

// The engagement "personality" badge shares the beta allowlist with Zoom, so it
// must gate to exactly the same cohort per environment (see beta-access.ts).
const STAGING_URL = "https://staging-app.cosmaneura.com/"
const PROD_URL = "https://app.cosmaneura.com/"

describe("isEngagementEnabled", () => {
  afterEach(() => vi.unstubAllEnvs())

  it("enables prod beta accounts in production", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isEngagementEnabled("greg@mycatholicdoctor.com")).toBe(true)
  })

  it("disables non-allowlisted doctors in production", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isEngagementEnabled("random@clinic.com")).toBe(false)
  })

  it("enables only the test account in staging", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    expect(isEngagementEnabled("zoom-reviewer@cosmaneura.com")).toBe(true)
    expect(isEngagementEnabled("greg@mycatholicdoctor.com")).toBe(false)
  })

  it("returns false for null or empty email", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isEngagementEnabled(null)).toBe(false)
    expect(isEngagementEnabled("")).toBe(false)
  })
})
