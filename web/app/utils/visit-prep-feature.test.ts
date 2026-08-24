import { afterEach, describe, expect, it, vi } from "vitest"

import { isVisitPrepEnabled } from "./visit-prep-feature"

const STAGING_URL = "https://staging-app.cosmaneura.com/"
const PROD_URL = "https://app.cosmaneura.com/"
const LOCAL_URL = "https://api.cosma.localhost/"

describe("isVisitPrepEnabled", () => {
  afterEach(() => vi.unstubAllEnvs())

  it("enables the internal cohort on staging", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    expect(isVisitPrepEnabled("john@cosmaneura.com")).toBe(true)
    expect(isVisitPrepEnabled("dev@cosmaneura.com")).toBe(true)
  })

  it("disables everyone else on staging", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    expect(isVisitPrepEnabled("stranger@example.com")).toBe(false)
  })

  // The MyCatholicDoctor pilot accounts are Visit Prep's launch cohort, but
  // they are added only once the feature works end to end. Until then their
  // membership in other allowlists must not leak access here.
  it("does not yet enable the MyCatholicDoctor pilot on staging", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    expect(isVisitPrepEnabled("greg@mycatholicdoctor.com")).toBe(false)
    expect(isVisitPrepEnabled("kathleen@mycatholicdoctor.com")).toBe(false)
  })

  it("matches emails case-insensitively", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    expect(isVisitPrepEnabled("John@Cosmaneura.com")).toBe(true)
  })

  // Production stays closed until the staging pilot validates the feature. A
  // Visit Prep tab rendered in production today points at an interview that
  // does not exist yet, so assert the closed door directly rather than
  // trusting whichever list this module happens to read.
  it("is closed to everyone in production, including the staging cohort", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isVisitPrepEnabled("john@cosmaneura.com")).toBe(false)
    expect(isVisitPrepEnabled("dev@cosmaneura.com")).toBe(false)
    expect(isVisitPrepEnabled("stranger@example.com")).toBe(false)
  })

  // The shared beta allowlist in beta-access.ts already carries a *production*
  // cohort for Zoom and the engagement badge — the MyCatholicDoctor accounts
  // among them. Visit Prep must not inherit production access from it.
  it("does not inherit access from the shared beta allowlist", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isVisitPrepEnabled("greg@mycatholicdoctor.com")).toBe(false)
    expect(isVisitPrepEnabled("kathleen@mycatholicdoctor.com")).toBe(false)
    expect(isVisitPrepEnabled("sweeman@fiathealth.org")).toBe(false)
    expect(isVisitPrepEnabled("christa.thornberry@mycatholicdoctor.com")).toBe(
      false,
    )
  })

  it("is ungated in local dev", () => {
    vi.stubEnv("VITE_PYTHON_URL", LOCAL_URL)
    expect(isVisitPrepEnabled("anyone@example.com")).toBe(true)
  })

  it("returns false for null or empty email", () => {
    vi.stubEnv("VITE_PYTHON_URL", LOCAL_URL)
    expect(isVisitPrepEnabled(null)).toBe(false)
    expect(isVisitPrepEnabled(undefined)).toBe(false)
    expect(isVisitPrepEnabled("")).toBe(false)
  })
})
