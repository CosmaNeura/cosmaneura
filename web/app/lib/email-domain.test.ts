import { describe, expect, it } from "vitest"

import {
  MCD_EMAIL_DOMAIN,
  emailMatchesDomain,
  extractEmailDomain,
  isMcdEmail,
} from "./email-domain"

describe("email domain matching", () => {
  it("extracts a normalized domain", () => {
    expect(extractEmailDomain("Claire@MyCatholicDoctor.com")).toBe(
      MCD_EMAIL_DOMAIN,
    )
  })

  it("allows exact domain and subdomains, not lookalikes", () => {
    expect(
      emailMatchesDomain("np@nashua.mycatholicdoctor.com", MCD_EMAIL_DOMAIN),
    ).toBe(true)
    expect(
      emailMatchesDomain(
        "attacker@evil-mycatholicdoctor.com",
        MCD_EMAIL_DOMAIN,
      ),
    ).toBe(false)
    expect(emailMatchesDomain("doc@gmail.com", MCD_EMAIL_DOMAIN)).toBe(false)
  })

  it("identifies MCD inboxes", () => {
    expect(isMcdEmail("lead@mycatholicdoctor.com")).toBe(true)
    expect(isMcdEmail(null)).toBe(false)
    expect(emailMatchesDomain("a@b.com", null)).toBe(false)
  })
})
