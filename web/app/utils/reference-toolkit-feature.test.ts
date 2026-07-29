import { afterEach, describe, expect, it, vi } from "vitest"

import { isReferenceToolkitEnabled } from "./reference-toolkit-feature"

const STAGING_URL = "https://staging-app.cosmaneura.com/"
const PROD_URL = "https://app.cosmaneura.com/"

describe("isReferenceToolkitEnabled", () => {
  afterEach(() => vi.unstubAllEnvs())

  it("enables the pilot cohort on staging", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    expect(isReferenceToolkitEnabled("kathleen@mycatholicdoctor.com")).toBe(
      true,
    )
    expect(isReferenceToolkitEnabled("john@cosmaneura.com")).toBe(true)
  })

  it("disables everyone else on staging", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    expect(isReferenceToolkitEnabled("stranger@example.com")).toBe(false)
  })

  it("returns false for null or empty email", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isReferenceToolkitEnabled(null)).toBe(false)
    expect(isReferenceToolkitEnabled("")).toBe(false)
  })
})
