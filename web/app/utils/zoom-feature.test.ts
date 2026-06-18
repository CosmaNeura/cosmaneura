import { afterEach, describe, expect, it, vi } from "vitest"

import { isZoomEnabled } from "./zoom-feature"

// Staging and production have opposite rules, so the environment must be
// detected reliably. import.meta.env.MODE is always "production" in deployed
// builds (react-router build with no --mode), so staging is detected from
// VITE_PYTHON_URL instead.
const STAGING_URL = "https://staging-app.cosmaneura.com/"
const PROD_URL = "https://app.cosmaneura.com/"

describe("isZoomEnabled", () => {
  afterEach(() => vi.unstubAllEnvs())

  describe("staging — only the dedicated test account", () => {
    it("enables zoom-reviewer@", () => {
      vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
      expect(isZoomEnabled("zoom-reviewer@cosmaneura.com")).toBe(true)
    })

    it("disables everyone else", () => {
      vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
      expect(isZoomEnabled("dev@cosmaneura.com")).toBe(false)
      expect(isZoomEnabled("dr.real@clinic.com")).toBe(false)
    })
  })

  describe("production — everyone except the test account", () => {
    it("enables real doctor accounts", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      expect(isZoomEnabled("dr.real@clinic.com")).toBe(true)
      expect(isZoomEnabled("dev@cosmaneura.com")).toBe(true)
    })

    it("disables the staging test account", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      expect(isZoomEnabled("zoom-reviewer@cosmaneura.com")).toBe(false)
    })

    it("is case-insensitive for the test account exclusion", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      expect(isZoomEnabled("Zoom-Reviewer@Cosmaneura.com")).toBe(false)
    })
  })

  it("returns false for null or empty email", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isZoomEnabled(null)).toBe(false)
    expect(isZoomEnabled("")).toBe(false)
  })
})
