import { describe, expect, it } from "vitest"

import { SIGN_IN_LOAD_ERROR_COPY, loginCopy } from "./channel"

describe("loginCopy", () => {
  it("tells MCD pilots to use Google or one email code, not password or resend", () => {
    const copy = loginCopy("mcd")

    expect(copy.kicker).toBe("MyCatholicDoctor")
    expect(copy.methodHint).toMatch(/Google or one email code/i)
    expect(copy.methodHint).toMatch(/newest mail/i)
    expect(copy.methodHint).toMatch(/Do not tap resend/)
    expect(copy.methodHint).toMatch(/no password for this group/i)
    expect(copy.methodHint).toMatch(/password box is a dead end/i)
  })

  it("does not attach MCD passwordless hints to other channels", () => {
    expect(loginCopy("direct").methodHint).toBeUndefined()
    expect(loginCopy("catholic").methodHint).toBeUndefined()
  })

  it("exposes the fail-loud sign-in load copy", () => {
    expect(SIGN_IN_LOAD_ERROR_COPY).toBe("sign-in didn’t load — refresh")
  })
})
