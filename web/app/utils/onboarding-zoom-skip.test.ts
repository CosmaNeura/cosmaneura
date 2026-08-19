import { afterEach, describe, expect, it, vi } from "vitest"

import { canSkipZoomOnboarding } from "./onboarding-zoom-skip"

const STAGING_URL = "https://staging-app.cosmaneura.com/"
const PROD_URL = "https://app.cosmaneura.com/"

describe("canSkipZoomOnboarding", () => {
  afterEach(() => vi.unstubAllEnvs())

  it("allows an allowlisted pilot when staging explicitly enables the switch", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    vi.stubEnv("VITE_STAGING_PILOT_ZOOM_SKIP", "true")

    expect(canSkipZoomOnboarding("john@cosmaneura.com")).toBe(true)
  })

  it("denies production even if the switch is accidentally enabled there", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    vi.stubEnv("VITE_STAGING_PILOT_ZOOM_SKIP", "true")

    // John is also production-beta allowlisted, so the host boundary is what
    // must fail closed in this case.
    expect(canSkipZoomOnboarding("john@cosmaneura.com")).toBe(false)
  })

  it("is off by default and keeps the existing onboarding flow", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    vi.stubEnv("VITE_STAGING_PILOT_ZOOM_SKIP", "false")

    expect(canSkipZoomOnboarding("john@cosmaneura.com")).toBe(false)
  })

  it("does not extend the skip to users outside the staging pilot cohort", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    vi.stubEnv("VITE_STAGING_PILOT_ZOOM_SKIP", "true")

    expect(canSkipZoomOnboarding("stranger@example.com")).toBe(false)
    expect(canSkipZoomOnboarding(null)).toBe(false)
  })
})
