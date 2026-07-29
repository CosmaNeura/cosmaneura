import { afterEach, describe, expect, it, vi } from "vitest"

import { isZoomEnabled } from "./zoom-feature"

// Staging and production have separate allowlists, so the environment must be
// detected reliably. import.meta.env.MODE is always "production" in deployed
// builds (react-router build with no --mode), so staging is detected from
// VITE_PYTHON_URL instead.
const STAGING_URL = "https://staging-app.cosmaneura.com/"
const PROD_URL = "https://app.cosmaneura.com/"

const PROD_ALLOWED = [
  "dev@cosmaneura.com",
  "greg@mycatholicdoctor.com",
  "kathleen@mycatholicdoctor.com",
  "john@cosmaneura.com",
  "ortegaoscar14@gmail.com",
]

describe("isZoomEnabled", () => {
  afterEach(() => vi.unstubAllEnvs())

  describe("staging — only the test account", () => {
    it("enables zoom-reviewer@", () => {
      vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
      expect(isZoomEnabled("zoom-reviewer@cosmaneura.com")).toBe(true)
    })

    it("enables the pilot cohort on staging", () => {
      vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
      expect(isZoomEnabled("john@cosmaneura.com")).toBe(true)
      expect(isZoomEnabled("kathleen@mycatholicdoctor.com")).toBe(true)
    })

    it("disables non-cohort users on staging", () => {
      vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
      expect(isZoomEnabled("sweeman@fiathealth.org")).toBe(false)
      expect(isZoomEnabled("stranger@example.com")).toBe(false)
    })
  })

  describe("production — only the explicit allowlist", () => {
    it("enables every allowlisted email", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      for (const email of PROD_ALLOWED) {
        expect(isZoomEnabled(email)).toBe(true)
      }
    })

    it("disables the staging test account", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      expect(isZoomEnabled("zoom-reviewer@cosmaneura.com")).toBe(false)
    })

    it("disables anyone not on the allowlist", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      expect(isZoomEnabled("random@clinic.com")).toBe(false)
    })

    it("is case-insensitive", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      expect(isZoomEnabled("Greg@MyCatholicDoctor.com")).toBe(true)
    })
  })

  it("returns false for null or empty email", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isZoomEnabled(null)).toBe(false)
    expect(isZoomEnabled("")).toBe(false)
  })
})
