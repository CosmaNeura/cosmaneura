import { describe, expect, it, vi } from "vitest"

import { createBillingCheckout } from "~/lib/practice-api"
import type { BillingSeatSummary, PracticeStatus } from "~/lib/practice-api"

import { PracticeJoinPanel, billingActionKind } from "./PracticeJoinPanel"
import { fireEvent, render, screen } from "@testing-library/react"

vi.mock("~/lib/practice-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/lib/practice-api")>()),
  createBillingCheckout: vi.fn(),
}))

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
    access_authorized: true,
    authorization_basis: "workforce_attestation",
    access_revoked: false,
    access_ends_at: null,
    pilot_access_grant_active: false,
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
    seat_summary: null,
    ...overrides,
  }
}

describe("PracticeJoinPanel billing recovery", () => {
  it("hides controls while the explicit billing rollout is disabled", () => {
    expect(billingActionKind(practice({ billing_enabled: false }), NOW)).toBe(
      "none",
    )
  })

  it("shows an honest recovery state when a lapsed owner cannot check out", () => {
    const expired = practice({ billing_enabled: false, entitled: false })
    expect(billingActionKind(expired, NOW)).toBe("checkout-unavailable")
    render(<PracticeJoinPanel practice={expired} showBillingActions />)
    expect(screen.getByText(/practice access is paused/i)).toBeInTheDocument()
    expect(
      screen.getByRole("button", {
        name: /reactivation temporarily unavailable/i,
      }),
    ).toBeDisabled()
    expect(screen.getByText(/records remain stored/i)).toBeInTheDocument()
  })

  it("tells lapsed workforce to contact the owner without exposing billing controls", () => {
    render(
      <PracticeJoinPanel
        practice={practice({ role: "workforce", entitled: false })}
        showBillingActions
      />,
    )
    expect(screen.getByText(/contact your practice owner/i)).toBeInTheDocument()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
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

  it("shows truthful pilot access and suppresses BAA and checkout prompts", () => {
    const grandfathered = practice({
      baa_accepted: false,
      access_authorized: false,
      authorization_basis: null,
      pilot_access_grant_active: true,
      entitled: false,
      trial_ends_at: "2026-08-19T20:00:00Z",
    })

    expect(billingActionKind(grandfathered, NOW)).toBe("none")
    render(<PracticeJoinPanel practice={grandfathered} showBillingActions />)
    expect(
      screen.getByText(/grandfathered pilot access is active/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/no billing required/i)).toBeInTheDocument()
    expect(
      screen.getByText(/no personal BAA attestation is required/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/BAA is not in force yet/i),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /reactivate cosmas/i }),
    ).not.toBeInTheDocument()
  })

  it("keeps an existing pilot subscription visible and manageable", () => {
    const grandfathered = practice({
      pilot_access_grant_active: true,
      billing_subscription_manageable: true,
      billing_portal_available: true,
      subscription_status: "active",
    })

    expect(billingActionKind(grandfathered, NOW)).toBe("portal")
    render(<PracticeJoinPanel practice={grandfathered} showBillingActions />)
    expect(
      screen.getByText(/existing subscription remains active/i),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /manage billing/i }),
    ).toBeInTheDocument()
    expect(screen.queryByText(/no billing required/i)).not.toBeInTheDocument()
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

  it("keeps checkout open during the final 48 hours with truthful copy", () => {
    const shortTrial = practice({ trial_ends_at: "2026-08-22T19:00:00Z" })
    expect(billingActionKind(shortTrial, NOW)).toBe("checkout")
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    try {
      render(<PracticeJoinPanel practice={shortTrial} showBillingActions />)
      expect(
        screen.getByRole("button", { name: /add payment now/i }),
      ).toBeEnabled()
      expect(
        screen.getByText(/trial is extended—never shortened/i),
      ).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it("carries the interrupted route into Checkout", () => {
    vi.mocked(createBillingCheckout).mockReturnValue(new Promise(() => {}))
    render(
      <PracticeJoinPanel
        practice={practice()}
        showBillingActions
        billingReturnTo="/schedule?view=week#today"
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: /add payment now/i }))

    expect(createBillingCheckout).toHaveBeenCalledWith(
      "/schedule?view=week#today",
    )
  })

  it("honors the legacy defer signal during a mixed-version rollout", () => {
    const legacyResponse = practice({
      billing_checkout_available_at: "2026-08-21T20:00:00Z",
    })
    expect(billingActionKind(legacyResponse, NOW)).toBe("deferred")
  })

  it("describes roster authorization without claiming personal attestation", () => {
    render(
      <PracticeJoinPanel
        practice={practice({
          role: "clinician",
          attested: false,
          authorization_basis: "organization_roster",
        })}
      />,
    )

    expect(
      screen.getByText(
        /access is authorized through the organization’s approved roster/i,
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/you did not personally attest/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/you attested under this practice/i),
    ).not.toBeInTheDocument()
  })

  it("does not offer reusable enrollment when no join code is available", () => {
    render(<PracticeJoinPanel practice={practice({ join_code: null })} />)

    expect(
      screen.getByText(/does not accept reusable join-code enrollment/i),
    ).toBeInTheDocument()
    expect(screen.queryByText("TESTCODE")).not.toBeInTheDocument()
  })
})

function seatSummary(
  overrides: Partial<BillingSeatSummary> = {},
): BillingSeatSummary {
  return {
    account_kind: "practice",
    active_clinicians: 4,
    billed_seats: 4,
    seat_sync_pending: false,
    unit_amount: 8900,
    currency: "usd",
    interval: "month",
    ...overrides,
  }
}

function subscribed(
  summary: Partial<BillingSeatSummary> = {},
  overrides: Partial<PracticeStatus> = {},
): PracticeStatus {
  return practice({
    join_code: null,
    subscription_status: "active",
    billing_subscription_manageable: true,
    billing_portal_available: true,
    seat_summary: seatSummary(summary),
    ...overrides,
  })
}

describe("PracticeJoinPanel seat summary", () => {
  it("shows the seats a practice pays for and their monthly cost", () => {
    render(<PracticeJoinPanel practice={subscribed()} showBillingActions />)

    expect(
      screen.getByText("You are paying for 4 seats · $356/month"),
    ).toBeInTheDocument()
    expect(screen.queryByText(/being updated/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/of 20/i)).not.toBeInTheDocument()
  })

  it("labels a solo doctor as a solo practice", () => {
    render(
      <PracticeJoinPanel
        practice={subscribed({
          account_kind: "solo",
          active_clinicians: 1,
          billed_seats: 1,
        })}
        showBillingActions
      />,
    )

    expect(
      screen.getByText("Solo practice · 1 seat · $89/month"),
    ).toBeInTheDocument()
  })

  it("says the cost starts after the trial while trialing", () => {
    render(
      <PracticeJoinPanel
        practice={subscribed({}, { subscription_status: "trialing" })}
        showBillingActions
      />,
    )

    expect(
      screen.getByText(
        "Your subscription covers 4 seats · $356/month after your trial",
      ),
    ).toBeInTheDocument()
  })

  it("says plainly when billed seats lag behind active clinicians", () => {
    render(
      <PracticeJoinPanel
        practice={subscribed({ billed_seats: 3, seat_sync_pending: true })}
        showBillingActions
      />,
    )

    expect(
      screen.getByText("You are paying for 3 seats · $267/month"),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /you have 4 active clinicians\. your billed seats are being updated to match/i,
      ),
    ).toBeInTheDocument()
  })

  it("does not show a stale cost while Stripe has not confirmed the new count", () => {
    render(
      <PracticeJoinPanel
        practice={subscribed({ billed_seats: null, seat_sync_pending: true })}
        showBillingActions
      />,
    )

    expect(screen.queryByText(/you are paying for/i)).not.toBeInTheDocument()
    expect(
      screen.getByText(/your billed seats are being updated to 4 seats/i),
    ).toBeInTheDocument()
  })

  it.each(["past_due", "unpaid", "paused", "incomplete"])(
    "does not claim a %s practice is paying",
    (subscription_status) => {
      render(
        <PracticeJoinPanel
          practice={subscribed({}, { subscription_status })}
          showBillingActions
        />,
      )

      expect(
        screen.getByText("Your subscription covers 4 seats · $356/month"),
      ).toBeInTheDocument()
      expect(screen.queryByText(/you are paying/i)).not.toBeInTheDocument()
    },
  )

  it("renders nothing when there is neither a billed count nor a pending update", () => {
    render(
      <PracticeJoinPanel
        practice={subscribed(
          { billed_seats: null },
          { subscription_status: "invalid" },
        )}
        showBillingActions
      />,
    )

    expect(screen.queryByTestId("seat-summary")).not.toBeInTheDocument()
  })

  it("shows the cost of a subscription kept alongside pilot access", () => {
    render(
      <PracticeJoinPanel
        practice={subscribed({}, { pilot_access_grant_active: true })}
        showBillingActions
      />,
    )

    expect(
      screen.getByText("You are paying for 4 seats · $356/month"),
    ).toBeInTheDocument()
  })

  it.each([
    ["trial before checkout", {}],
    [
      "pilot access without a subscription",
      { pilot_access_grant_active: true },
    ],
    [
      "complimentary access",
      { billing_exempt: true, billing_exempt_active: true },
    ],
    [
      "ended complimentary access",
      { billing_exempt: true, billing_exempt_until: "2026-08-19T20:00:00Z" },
    ],
    ["canceled subscription", { subscription_status: "canceled" }],
  ] satisfies [string, Partial<PracticeStatus>][])(
    "shows no billed seats for %s",
    (_label, overrides) => {
      render(
        <PracticeJoinPanel
          practice={practice({
            seat_summary: seatSummary({ billed_seats: null }),
            ...overrides,
          })}
          showBillingActions
        />,
      )

      expect(screen.queryByTestId("seat-summary")).not.toBeInTheDocument()
    },
  )

  it("does not show billing seats to workforce", () => {
    render(
      <PracticeJoinPanel
        practice={subscribed({}, { role: "workforce" })}
        showBillingActions
      />,
    )

    expect(screen.queryByTestId("seat-summary")).not.toBeInTheDocument()
  })

  it("shows seats only on the billing screen", () => {
    render(<PracticeJoinPanel practice={subscribed()} />)

    expect(screen.queryByTestId("seat-summary")).not.toBeInTheDocument()
  })
})
