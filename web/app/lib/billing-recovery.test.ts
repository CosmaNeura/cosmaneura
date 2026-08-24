import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  BillingRecoveryRedirectError,
  billingRecoveryLocation,
  handleInternalApiResponse,
  isBillingRecoveryRedirectError,
  isInternalApiRequest,
  restrictSettingsToBillingRecovery,
  resetBillingRecoveryRedirectState,
  safeBillingReturnTo,
} from "./billing-recovery"

beforeEach(() => resetBillingRecoveryRedirectState())

describe("billing recovery settings mode", () => {
  it("hides gated controls only until practice entitlement is restored", () => {
    expect(restrictSettingsToBillingRecovery(true, false)).toBe(true)
    expect(restrictSettingsToBillingRecovery(true, null)).toBe(true)
    expect(restrictSettingsToBillingRecovery(true, undefined)).toBe(true)
    expect(restrictSettingsToBillingRecovery(true, true)).toBe(false)
    expect(restrictSettingsToBillingRecovery(false, false)).toBe(false)
  })
})

describe("billing recovery routing", () => {
  it("preserves the current path, query, and hash", () => {
    expect(
      billingRecoveryLocation(
        "https://app.cosmaneura.com/notes/note_123?tab=soap#assessment",
      ),
    ).toBe(
      "/settings?billing=required&returnTo=%2Fnotes%2Fnote_123%3Ftab%3Dsoap%23assessment",
    )
  })

  it("keeps return navigation same-origin", () => {
    expect(safeBillingReturnTo("/schedule?view=week#today")).toBe(
      "/schedule?view=week#today",
    )
    expect(safeBillingReturnTo("https://evil.example")).toBe("/notes")
    expect(safeBillingReturnTo("//evil.example/path")).toBe("/notes")
    expect(safeBillingReturnTo("/\\evil.example/path")).toBe("/notes")
  })

  it("redirects once for concurrent 402 responses and throws a typed fallback", () => {
    const navigate = vi.fn()
    const first = () =>
      handleInternalApiResponse(
        new Response(null, { status: 402 }),
        "https://app.cosmaneura.com/schedule?view=week#today",
        navigate,
      )
    const second = () =>
      handleInternalApiResponse(
        new Response(null, { status: 402 }),
        "https://app.cosmaneura.com/notes",
        navigate,
      )

    expect(first).toThrow(BillingRecoveryRedirectError)
    expect(second).toThrow(BillingRecoveryRedirectError)
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith(
      "/settings?billing=required&returnTo=%2Fschedule%3Fview%3Dweek%23today",
    )

    try {
      first()
    } catch (error) {
      expect(isBillingRecoveryRedirectError(error)).toBe(true)
    }
  })

  it("does not redirect-loop on settings and preserves its normal 402 response", () => {
    const response = new Response(null, { status: 402 })
    const navigate = vi.fn()

    expect(
      handleInternalApiResponse(
        response,
        "https://app.cosmaneura.com/settings?billing=required",
        navigate,
      ),
    ).toBe(response)
    expect(navigate).not.toHaveBeenCalled()
  })

  it("leaves non-402 responses unchanged", () => {
    const response = new Response(null, { status: 500 })
    const navigate = vi.fn()

    expect(
      handleInternalApiResponse(
        response,
        "https://app.cosmaneura.com/notes",
        navigate,
      ),
    ).toBe(response)
    expect(navigate).not.toHaveBeenCalled()
  })

  it("recognizes only internal API paths", () => {
    const current = "https://app.cosmaneura.com/notes"
    expect(
      isInternalApiRequest(
        "https://api.cosmaneura.com/internal/v1/settings",
        current,
      ),
    ).toBe(true)
    expect(isInternalApiRequest("/internal/v1/notes", current)).toBe(true)
    expect(isInternalApiRequest("/external/chat/intake/123", current)).toBe(
      false,
    )
    expect(
      isInternalApiRequest("/embed/v1/context-tokens/exchange", current),
    ).toBe(false)
    expect(
      isInternalApiRequest(
        "https://storage.example/container/blob.pdf",
        current,
      ),
    ).toBe(false)
  })
})
