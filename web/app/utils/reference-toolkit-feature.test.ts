import { afterEach, describe, expect, it, vi } from "vitest"

import { isReferenceToolkitEnabled } from "./reference-toolkit-feature"

const STAGING_URL = "https://staging-app.cosmaneura.com/"
const PROD_URL = "https://app.cosmaneura.com/"
const LOCAL_URL = "https://api.cosma.localhost/"

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

  it("matches emails case-insensitively", () => {
    vi.stubEnv("VITE_PYTHON_URL", STAGING_URL)
    expect(isReferenceToolkitEnabled("John@Cosmaneura.com")).toBe(true)
  })

  // The server keeps its production cohort empty until the staging pilot
  // validates the feature. If the UI renders for a production user, they get a
  // panel whose every request 404s — so assert the closed door directly rather
  // than trusting whichever list this module happens to read.
  it("is closed to everyone in production, including the staging cohort", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isReferenceToolkitEnabled("john@cosmaneura.com")).toBe(false)
    expect(isReferenceToolkitEnabled("kathleen@mycatholicdoctor.com")).toBe(
      false,
    )
    expect(isReferenceToolkitEnabled("stranger@example.com")).toBe(false)
  })

  // Production users on the *shared* beta allowlist for other features (Zoom,
  // the engagement badge) must not inherit toolkit access from it.
  it("does not inherit access from the shared beta allowlist", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isReferenceToolkitEnabled("sweeman@fiathealth.org")).toBe(false)
    expect(
      isReferenceToolkitEnabled("christa.thornberry@mycatholicdoctor.com"),
    ).toBe(false)
  })

  it("is ungated in local dev, matching the server", () => {
    vi.stubEnv("VITE_PYTHON_URL", LOCAL_URL)
    expect(isReferenceToolkitEnabled("anyone@example.com")).toBe(true)
  })

  it("returns false for null or empty email", () => {
    vi.stubEnv("VITE_PYTHON_URL", PROD_URL)
    expect(isReferenceToolkitEnabled(null)).toBe(false)
    expect(isReferenceToolkitEnabled("")).toBe(false)
  })
})
