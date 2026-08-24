import { describe, expect, it } from "vitest"

import { isAccountRecoveryRequired } from "./account-recovery"

describe("account recovery response", () => {
  it("recognizes only the explicit 409 recovery detail", () => {
    const error = {
      detail:
        "This email is already linked to a previous sign-in. Contact support to recover the existing account; no records have been lost.",
    }

    expect(isAccountRecoveryRequired(error, 409)).toBe(true)
    expect(isAccountRecoveryRequired(error, 403)).toBe(false)
    expect(isAccountRecoveryRequired({ detail: "Other conflict" }, 409)).toBe(
      false,
    )
  })
})
