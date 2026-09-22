import { data } from "react-router"
import { describe, expect, it } from "vitest"

import {
  PRACTICE_BAA_REQUIRED_DETAIL,
  PRACTICE_MEMBERSHIP_REQUIRED_DETAIL,
  PracticeAgreementError,
  practiceAgreementBlockFromClientResult,
  practiceAgreementBlockFromRouteFailure,
  practiceAgreementBlockFromUnknown,
} from "./practice-gate-error"

describe("practice agreement failures", () => {
  it.each([
    [PRACTICE_MEMBERSHIP_REQUIRED_DETAIL, "join_practice", "Join the practice"],
    [
      PRACTICE_BAA_REQUIRED_DETAIL,
      "accept_agreement",
      "Accept the updated agreement",
    ],
  ])(
    "maps the known 403 detail %s to its recovery action",
    (detail, kind, headline) => {
      const block = practiceAgreementBlockFromUnknown({ status: 403, detail })
      expect(block).toMatchObject({ kind, headline, detail })
    },
  )

  it("recognizes a returned SDK error without throwing", () => {
    expect(
      practiceAgreementBlockFromClientResult({
        error: { detail: PRACTICE_MEMBERSHIP_REQUIRED_DETAIL },
        response: { status: 403 },
      })?.kind,
    ).toBe("join_practice")
  })

  it("recognizes a nested thrown SDK error", () => {
    expect(
      practiceAgreementBlockFromUnknown({
        error: { detail: PRACTICE_BAA_REQUIRED_DETAIL },
        response: { status: 403 },
      })?.kind,
    ).toBe("accept_agreement")
  })

  it.each([undefined, 200, 401, 402, 500])(
    "rejects matching copy with status %s",
    (status) => {
      const error = { detail: PRACTICE_BAA_REQUIRED_DETAIL }
      expect(practiceAgreementBlockFromUnknown({ status, error })).toBeNull()
      expect(
        practiceAgreementBlockFromClientResult({ error, response: { status } }),
      ).toBeNull()
      expect(
        practiceAgreementBlockFromRouteFailure({ status, data: error }),
      ).toBeNull()
    },
  )

  it.each([
    { status: 403 },
    { status: 403, detail: "Some unrelated denial" },
    {
      status: 403,
      detail: `Unrelated failure: ${PRACTICE_BAA_REQUIRED_DETAIL}`,
    },
    { status: 403, message: PRACTICE_BAA_REQUIRED_DETAIL },
    {
      status: 500,
      response: { status: 403 },
      error: { detail: PRACTICE_BAA_REQUIRED_DETAIL },
    },
    new Error(PRACTICE_BAA_REQUIRED_DETAIL),
  ])(
    "does not infer an agreement problem from an unrelated error: %j",
    (error) => {
      expect(practiceAgreementBlockFromUnknown(error)).toBeNull()
    },
  )

  it("does not treat a successful notes payload as blocked", () => {
    expect(
      practiceAgreementBlockFromClientResult({
        data: { appointments: [] },
        error: { detail: PRACTICE_BAA_REQUIRED_DETAIL },
        response: { status: 200 },
      }),
    ).toBeNull()
  })

  it("retains the block through typed errors and React Router wrappers", () => {
    const block = practiceAgreementBlockFromUnknown({
      status: 403,
      detail: PRACTICE_BAA_REQUIRED_DETAIL,
    })!
    for (const error of [
      new PracticeAgreementError(block),
      { status: 403, statusText: "Forbidden", data: block },
      data(block, { status: 403 }),
    ]) {
      expect(practiceAgreementBlockFromRouteFailure(error)).toEqual(block)
    }
    expect(
      practiceAgreementBlockFromRouteFailure(data(block, { status: 500 })),
    ).toBeNull()
  })
})
