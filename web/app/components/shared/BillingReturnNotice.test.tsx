import { MemoryRouter } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import { getPractice } from "~/lib/practice-api"
import type { PracticeStatus } from "~/lib/practice-api"

import { BillingReturnNotice, safeBillingReturnTo } from "./BillingReturnNotice"
import { act, render, screen } from "@testing-library/react"

vi.mock("~/lib/practice-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/lib/practice-api")>()),
  getPractice: vi.fn(),
}))

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
    pilot_access_grant_active: false,
    trial_ends_at: "2026-08-20T20:00:00Z",
    billing_exempt: false,
    billing_exempt_until: null,
    billing_exempt_active: false,
    billing_enabled: true,
    billing_management_enabled: true,
    billing_portal_available: false,
    billing_checkout_resumable: false,
    billing_subscription_manageable: false,
    billing_checkout_available_at: null,
    entitled: false,
    subscription_status: null,
    ...overrides,
  }
}

function renderNotice(
  entry: string,
  currentPractice: PracticeStatus,
  onPracticeChange = vi.fn(),
) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <BillingReturnNotice
        practice={currentPractice}
        onPracticeChange={onPracticeChange}
      />
    </MemoryRouter>,
  )
  return onPracticeChange
}

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe("BillingReturnNotice", () => {
  it("keeps return navigation on this application", () => {
    expect(safeBillingReturnTo("/schedule?view=week")).toBe(
      "/schedule?view=week",
    )
    expect(safeBillingReturnTo("https://evil.example")).toBe("/notes")
    expect(safeBillingReturnTo("//evil.example/path")).toBe("/notes")
    expect(safeBillingReturnTo("/\\evil.example/path")).toBe("/notes")
  })

  it("explains a canceled checkout without claiming an access change", () => {
    renderNotice("/settings?billing=cancelled", practice())
    expect(screen.getByText(/checkout canceled/i)).toBeInTheDocument()
    expect(
      screen.getByText(/no billing changes were made/i),
    ).toBeInTheDocument()
  })

  it("shows restored access when the return already reflects entitlement", () => {
    const restored = practice({
      entitled: true,
      subscription_status: "active",
    })
    const onPracticeChange = renderNotice(
      "/settings?billing=success&returnTo=%2Fschedule",
      restored,
    )
    expect(screen.getByText(/practice access restored/i)).toBeInTheDocument()
    expect(onPracticeChange).toHaveBeenCalledWith(restored)
    expect(
      screen.getByRole("button", { name: /continue to cosmas/i }),
    ).toBeInTheDocument()
  })

  it("waits for the webhook before claiming access is restored", async () => {
    vi.useFakeTimers()
    const restored = practice({
      entitled: true,
      subscription_status: "active",
    })
    vi.mocked(getPractice).mockResolvedValue(restored)
    const onPracticeChange = renderNotice(
      "/settings?billing=success",
      practice(),
    )

    expect(
      screen.getByText(/confirming your subscription/i),
    ).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500)
    })

    expect(getPractice).toHaveBeenCalledTimes(1)
    expect(onPracticeChange).toHaveBeenCalledWith(restored)
    expect(screen.getByText(/practice access restored/i)).toBeInTheDocument()
  })

  it("does not invite a duplicate payment when confirmation is delayed", async () => {
    vi.useFakeTimers()
    vi.mocked(getPractice).mockResolvedValue(practice())
    renderNotice("/settings?billing=success", practice())

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })

    expect(
      screen.getByText(/subscription confirmation is taking longer/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/do not pay a second time/i)).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /check again/i }),
    ).toBeInTheDocument()
  })
})
