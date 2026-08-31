import { describe, expect, it } from "vitest"

import {
  PRACTICE_BAA_REQUIRED_DETAIL,
  PRACTICE_MEMBERSHIP_REQUIRED_DETAIL,
  practiceAgreementBlockFromClientResult,
  practiceAgreementBlockFromRouteFailure,
  practiceAgreementBlockFromUnknown,
} from "./practice-gate-error"

describe("practiceAgreementBlockFromUnknown", () => {
  it("maps the join-practice 403 detail from a hey-api error body", () => {
    const block = practiceAgreementBlockFromUnknown({
      detail: PRACTICE_MEMBERSHIP_REQUIRED_DETAIL,
    })

    expect(block?.kind).toBe("join_practice")
    expect(block?.headline).toBe("Join the practice")
    expect(block?.instruction).toMatch(/accept the updated agreement/i)
    expect(block?.instruction).toMatch(/join the practice/i)
    expect(block?.detail).toBe(PRACTICE_MEMBERSHIP_REQUIRED_DETAIL)
  })

  it("maps the re-accept 403 detail from a thrown Error", () => {
    const block = practiceAgreementBlockFromUnknown(
      new Error(PRACTICE_BAA_REQUIRED_DETAIL),
    )

    expect(block?.kind).toBe("accept_agreement")
    expect(block?.headline).toBe("Accept the updated agreement")
    expect(block?.instruction).toMatch(/accept the updated agreement/i)
    expect(block?.instruction).toMatch(/join the practice/i)
    expect(block?.detail).toBe(PRACTICE_BAA_REQUIRED_DETAIL)
  })

  it("reads a nested hey-api { error: { detail } } payload", () => {
    expect(
      practiceAgreementBlockFromUnknown({
        error: { detail: PRACTICE_BAA_REQUIRED_DETAIL },
      })?.kind,
    ).toBe("accept_agreement")
  })

  it("ignores unrelated failures", () => {
    expect(practiceAgreementBlockFromUnknown(new Error("network down"))).toBe(
      null,
    )
    expect(practiceAgreementBlockFromUnknown({ status: 403 })).toBe(null)
  })
})

describe("practiceAgreementBlockFromClientResult", () => {
  it("does not treat a successful notes payload as blocked", () => {
    expect(
      practiceAgreementBlockFromClientResult({
        data: { appointments: [] },
        error: undefined,
        response: { status: 200 },
      }),
    ).toBe(null)
  })

  it("surfaces a 403 that hey-api returned without throwing", () => {
    const block = practiceAgreementBlockFromClientResult({
      data: undefined,
      error: { detail: PRACTICE_MEMBERSHIP_REQUIRED_DETAIL },
      response: { status: 403 },
    })

    expect(block?.kind).toBe("join_practice")
  })
})

describe("practiceAgreementBlockFromRouteFailure", () => {
  it("reads a React Router 403 data payload", () => {
    const block = practiceAgreementBlockFromUnknown({
      detail: PRACTICE_BAA_REQUIRED_DETAIL,
    })

    expect(
      practiceAgreementBlockFromRouteFailure({
        status: 403,
        statusText: "Forbidden",
        data: block,
      })?.headline,
    ).toBe("Accept the updated agreement")
  })
})
