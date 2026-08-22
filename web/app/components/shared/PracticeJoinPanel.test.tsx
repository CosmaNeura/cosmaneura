import { describe, expect, it, vi } from "vitest"

import type { PracticeStatus } from "~/lib/practice-api"

import { PracticeJoinPanel, billingActionKind } from "./PracticeJoinPanel"
import { render, screen } from "@testing-library/react"

const NOW = new Date("2026-08-20T20:00:00Z")

function practice(overrides: Partial<PracticeStatus> = {}): PracticeStatus {
  return {
    legal_name: "Test Practice, PLLC",
    join_code: "TESTCODE",
    email_domain: "example.com",
    origin_channel: "direct",
    pastoral_pack: false,
    baa_accepted: true,
    role: "owner",
    attested: true,
    trial_ends_at: "2026-08-25T20:00:00Z",
    billing_exempt: false,
    billing_exempt_until: null,
    billing_exempt_active: false,
    billing_enabled: true,
    billing_management_enabled: true,
    billing_portal_available: false,
    billing_checkout_resumable: false,
    billing_subscription_manageable: false,
    billing_checkout_available_at: null,
    entitled: true,
    subscription_status: null,
    ...overrides,
  }
}

describe("PracticeJoinPanel billing recovery", () => {
  it("hides controls while the explicit billing rollout is disabled", () => {
    expect(billingActionKind(practice({ billing_enabled: false }), NOW)).toBe(
      "none",
    )
  })

  it("lets an expired timed comp reactivate instead of hiding billing", () => {
    const expired = practice({
      billing_exempt: true,
      billing_exempt_until: "2026-08-19T20:00:00Z",
      billing_exempt_active: false,
      entitled: false,
      trial_ends_at: "2026-08-19T20:00:00Z",
    })
    expect(billingActionKind(expired, NOW)).toBe("checkout")
    render(<PracticeJoinPanel practice={expired} showBillingActions />)
    expect(screen.getByText(/early-adopter access ended/i)).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /reactivate cosmas/i }),
    ).toBeInTheDocument()
  })

  it("sends past-due and paused subscriptions to the portal", () => {
    expect(
      billingActionKind(
        practice({
          subscription_status: "past_due",
          billing_subscription_manageable: true,
          entitled: false,
        }),
        NOW,
      ),
    ).toBe("portal")
  })

  it("keeps an existing subscription manageable when acquisition or comp says no", () => {
    expect(
      billingActionKind(
        practice({
          billing_enabled: false,
          billing_exempt_active: true,
          billing_subscription_manageable: true,
          subscription_status: "active",
        }),
        NOW,
      ),
    ).toBe("portal")
  })

  it("resumes an exact incomplete checkout even after acquisition is paused", () => {
    const incomplete = practice({
      billing_enabled: false,
      billing_checkout_resumable: true,
      billing_portal_available: true,
      billing_subscription_manageable: true,
      subscription_status: "incomplete",
      entitled: false,
    })
    expect(billingActionKind(incomplete, NOW)).toBe("checkout")
    render(<PracticeJoinPanel practice={incomplete} showBillingActions />)
    expect(
      screen.getByRole("button", { name: /resume checkout/i }),
    ).toBeInTheDocument()
  })

  it("shows a fail-closed maintenance warning instead of checkout", () => {
    const unavailable = practice({
      billing_enabled: false,
      billing_management_enabled: false,
      billing_subscription_manageable: true,
      subscription_status: "past_due",
    })
    expect(billingActionKind(unavailable, NOW)).toBe("portal-unavailable")
    render(<PracticeJoinPanel practice={unavailable} showBillingActions />)
    expect(
      screen.getByRole("button", { name: /manage billing unavailable/i }),
    ).toBeDisabled()
  })

  it("keeps the portal visible for a terminal customer when acquisition is off", () => {
    const canceled = practice({
      billing_enabled: false,
      billing_portal_available: true,
      subscription_status: "canceled",
      entitled: false,
    })
    render(<PracticeJoinPanel practice={canceled} showBillingActions />)
    expect(
      screen.getByRole("button", { name: /manage billing and invoices/i }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /reactivate cosmas/i }),
    ).not.toBeInTheDocument()
  })

  it("offers reactivation and prior invoices as separate terminal-customer actions", () => {
    const canceled = practice({
      billing_portal_available: true,
      subscription_status: "canceled",
      entitled: false,
    })
    render(<PracticeJoinPanel practice={canceled} showBillingActions />)
    expect(
      screen.getByRole("button", { name: /reactivate cosmas/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /manage billing and invoices/i }),
    ).toBeInTheDocument()
  })

  it("fails closed for a terminal customer when maintenance is unavailable", () => {
    const unavailable = practice({
      billing_enabled: false,
      billing_management_enabled: false,
      billing_portal_available: true,
      subscription_status: "canceled",
      entitled: false,
    })
    render(<PracticeJoinPanel practice={unavailable} showBillingActions />)
    expect(
      screen.getByRole("button", { name: /manage billing unavailable/i }),
    ).toBeDisabled()
  })

  it("defers checkout during the final 48 hours with truthful copy", () => {
    const deferred = practice({
      billing_checkout_available_at: "2026-08-21T20:00:00Z",
    })
    expect(billingActionKind(deferred, NOW)).toBe("deferred")
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    try {
      render(<PracticeJoinPanel practice={deferred} showBillingActions />)
      expect(
        screen.getByRole("button", { name: /checkout opens when trial ends/i }),
      ).toBeDisabled()
      expect(
        screen.getByText(/preserve every remaining trial hour/i),
      ).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})
