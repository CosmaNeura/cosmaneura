import { afterEach, describe, expect, it, vi } from "vitest"

import type { PracticeStatus } from "~/lib/practice-api"
import { createBillingCheckout, createBillingPortal } from "~/lib/practice-api"

import { PracticeJoinPanel, billingActionKind } from "./PracticeJoinPanel"
import { cleanup, render, screen } from "@testing-library/react"

vi.mock("~/lib/practice-api", () => ({
  createBillingCheckout: vi.fn(),
  createBillingPortal: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function practice(overrides: Partial<PracticeStatus> = {}): PracticeStatus {
  return {
    legal_name: "Synthetic Practice",
    join_code: null,
    email_domain: "synthetic.invalid",
    origin_channel: "direct",
    pastoral_pack: false,
    baa_accepted: true,
    role: "owner",
    attested: true,
    access_authorized: true,
    authorization_basis: "workforce_attestation",
    access_revoked: false,
    access_ends_at: null,
    access_ended: false,
    pilot_access_grant_active: false,
    trial_ends_at: null,
    billing_exempt: false,
    billing_exempt_until: null,
    billing_exempt_active: false,
    billing_enabled: false,
    billing_management_enabled: false,
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

const CASES: [string, Partial<PracticeStatus>, boolean][] = [
  [
    "active exempt",
    { billing_exempt: true, billing_exempt_active: true },
    false,
  ],
  ["legacy pilot", { pilot_access_grant_active: true, entitled: false }, false],
  [
    "paid",
    { subscription_status: "active", billing_subscription_manageable: true },
    false,
  ],
  ["legacy trial", { trial_ends_at: "2099-01-01T00:00:00Z" }, false],
  ["never paid without another entitlement", { entitled: false }, true],
]

describe("existing account with both Stripe readiness flags on", () => {
  it.each(CASES)(
    "preserves the existing access UI for %s",
    (_kind, overrides, paused) => {
      const before = practice(overrides)
      const view = render(
        <PracticeJoinPanel practice={before} showBillingActions />,
      )
      expect(screen.queryByRole("alert") !== null).toBe(paused)

      // These are the API readiness facts produced by both configured flags.
      // Entitlement, membership and grant facts are deliberately unchanged.
      const after = {
        ...before,
        billing_enabled: true,
        billing_management_enabled: true,
      }
      view.rerender(<PracticeJoinPanel practice={after} showBillingActions />)
      expect(screen.queryByRole("alert") !== null).toBe(paused)
      expect(
        screen.queryByRole("button", { name: /accept|join|onboard/i }),
      ).toBeNull()
      expect(createBillingCheckout).not.toHaveBeenCalled()
      expect(createBillingPortal).not.toHaveBeenCalled()
    },
  )

  it("keeps an active comp and a pilot out of acquisition billing", () => {
    expect(
      billingActionKind(
        practice({ billing_enabled: true, billing_exempt_active: true }),
      ),
    ).toBe("none")
    expect(
      billingActionKind(
        practice({ billing_enabled: true, pilot_access_grant_active: true }),
      ),
    ).toBe("none")
  })

  it("keeps the legacy pilot out of the pending-verification wall", () => {
    render(
      <PracticeJoinPanel
        practice={practice({
          pilot_access_grant_active: true,
          entitled: false,
          subscription_status: "pending_verification",
          billing_enabled: true,
          billing_management_enabled: true,
          billing_subscription_manageable: true,
        })}
        showBillingActions
      />,
    )
    expect(screen.queryByRole("alert")).toBeNull()
    expect(
      screen.getByText(/no personal BAA attestation is required/i),
    ).toBeInTheDocument()
  })

  it("characterizes the active-comp wall after a pending projection", () => {
    render(
      <PracticeJoinPanel
        practice={practice({
          billing_exempt: true,
          billing_exempt_active: true,
          entitled: false,
          subscription_status: "pending_verification",
          billing_enabled: true,
          billing_management_enabled: true,
        })}
        showBillingActions
      />,
    )
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Practice access is paused",
    )
  })

  it.fails(
    "Wall found: an active comp should not show a billing wall after pending verification",
    () => {
      render(
        <PracticeJoinPanel
          practice={practice({
            billing_exempt: true,
            billing_exempt_active: true,
            entitled: false,
            subscription_status: "pending_verification",
            billing_enabled: true,
            billing_management_enabled: true,
          })}
          showBillingActions
        />,
      )
      expect(screen.queryByRole("alert")).toBeNull()
    },
  )
})
