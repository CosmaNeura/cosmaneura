import { describe, expect, it } from "vitest"

import { APP_LOADING_DELAY_COPY, loginCopy } from "./channel"

describe("loginCopy", () => {
  it("explains available sign-in methods and email-code recovery", () => {
    const copy = loginCopy("mcd")

    expect(copy.kicker).toBe("MyCatholicDoctor")
    expect(copy.methodHint).toMatch(/sign-in method shown below/i)
    expect(copy.methodHint).toMatch(/newest message/i)
    expect(copy.methodHint).toMatch(/request another code when available/i)
    expect(copy.methodHint).not.toMatch(/no password/i)
    expect(copy.methodHint).not.toMatch(/dead end|do not.*resend/i)
  })

  it("does not attach MCD hints to other channels", () => {
    expect(loginCopy("direct").methodHint).toBeUndefined()
    expect(loginCopy("catholic").methodHint).toBeUndefined()
  })

  it("uses a neutral application-loading message", () => {
    expect(APP_LOADING_DELAY_COPY).toBe(
      "The application is taking longer than usual to load.",
    )
  })
})
