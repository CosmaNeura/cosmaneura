import { describe, expect, it } from "vitest"

import { isValidNpi } from "./npi"

describe("NPI validation", () => {
  it("requires ten digits with a valid CMS check digit", () => {
    expect(isValidNpi("1234567893")).toBe(true)
    expect(isValidNpi("1234567890")).toBe(false)
    expect(isValidNpi("12345678901")).toBe(false)
    expect(isValidNpi("12345A7893")).toBe(false)
    expect(isValidNpi("0000000000")).toBe(false)
  })
})
