import { afterEach, describe, expect, it, vi } from "vitest"

import { isBetaUserEnabled } from "./beta-access"

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

describe("isBetaUserEnabled", () => {
  afterEach(() => vi.unstubAllEnvs())

  describe("staging — only the test account", () => {
    it("enables zoom-reviewer@", () => {
      vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
      expect(isBetaUserEnabled("zoom-reviewer@cosmaneura.com")).toBe(true)
    })

    it("enables the pilot cohort on staging", () => {
      vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
      expect(isBetaUserEnabled("john@cosmaneura.com")).toBe(true)
      expect(isBetaUserEnabled("kathleen@mycatholicdoctor.com")).toBe(true)
    })

    it("disables non-cohort users on staging", () => {
      vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
      expect(isBetaUserEnabled("sweeman@fiathealth.org")).toBe(false)
      expect(isBetaUserEnabled("stranger@example.com")).toBe(false)
    })
  })

  describe("production — only the explicit allowlist", () => {
    it("enables every allowlisted email", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      for (const email of PROD_ALLOWED) {
        expect(isBetaUserEnabled(email)).toBe(true)
      }
    })

    it("disables the staging test account", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      expect(isBetaUserEnabled("zoom-reviewer@cosmaneura.com")).toBe(false)
    })

    it("disables anyone not on the allowlist", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      expect(isBetaUserEnabled("random@clinic.com")).toBe(false)
    })

    it("is case-insensitive", () => {
      vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
      expect(isBetaUserEnabled("Greg@MyCatholicDoctor.com")).toBe(true)
    })
  })

  it("returns false for null or empty email", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isBetaUserEnabled(null)).toBe(false)
    expect(isBetaUserEnabled("")).toBe(false)
  })
})
