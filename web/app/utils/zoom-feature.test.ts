import { afterEach, describe, expect, it, vi } from "vitest"

import { isZoomEnabled } from "./zoom-feature"

const isStaging = vi.fn()
vi.mock("~/utils/environment", () => ({
  isStaging: () => isStaging(),
}))

describe("isZoomEnabled", () => {
  afterEach(() => isStaging.mockReset())

  describe("in staging", () => {
    it("enables only the dedicated test account (zoom-reviewer@)", () => {
      isStaging.mockReturnValue(true)
      expect(isZoomEnabled("zoom-reviewer@cosmaneura.com")).toBe(true)
    })

    it("disables other accounts that are allowed in production", () => {
      isStaging.mockReturnValue(true)
      expect(isZoomEnabled("dev@cosmaneura.com")).toBe(false)
    })
  })

  describe("in production", () => {
    it("keeps the existing beta allowlist", () => {
      isStaging.mockReturnValue(false)
      expect(isZoomEnabled("dev@cosmaneura.com")).toBe(true)
      expect(isZoomEnabled("zoom-reviewer@cosmaneura.com")).toBe(true)
    })

    it("disables anyone not on the allowlist", () => {
      isStaging.mockReturnValue(false)
      expect(isZoomEnabled("random@cosmaneura.com")).toBe(false)
    })
  })

  it("returns false for null or empty email regardless of environment", () => {
    isStaging.mockReturnValue(false)
    expect(isZoomEnabled(null)).toBe(false)
    expect(isZoomEnabled("")).toBe(false)
  })
})
