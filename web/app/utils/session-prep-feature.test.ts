import { afterEach, describe, expect, it, vi } from "vitest"

import { isSessionPrepEnabled } from "./session-prep-feature"

const STAGING_URL = "https://staging-app.cosmaneura.com/"
const PROD_URL = "https://app.cosmaneura.com/"

describe("isSessionPrepEnabled", () => {
  afterEach(() => vi.unstubAllEnvs())

  it("enables the pilot cohort on staging", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    expect(isSessionPrepEnabled("john@cosmaneura.com")).toBe(true)
    expect(isSessionPrepEnabled("dev@cosmaneura.com")).toBe(true)
    expect(isSessionPrepEnabled("greg@mycatholicdoctor.com")).toBe(true)
    expect(isSessionPrepEnabled("kathleen@mycatholicdoctor.com")).toBe(true)
  })

  it("disables everyone else on staging", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    expect(isSessionPrepEnabled("stranger@example.com")).toBe(false)
  })

  it("returns false for null or empty email", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isSessionPrepEnabled(null)).toBe(false)
    expect(isSessionPrepEnabled("")).toBe(false)
  })
})
