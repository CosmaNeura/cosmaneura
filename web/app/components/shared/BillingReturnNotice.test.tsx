import { MemoryRouter } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import { getPractice } from "~/lib/practice-api"
import type { PracticeStatus } from "~/lib/practice-api"

import { BillingReturnNotice, safeBillingReturnTo } from "./BillingReturnNotice"
import { act, fireEvent, render, screen } from "@testing-library/react"

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
    access_ends_at: null,
    access_ended: false,
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
    seat_summary: null,
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
  vi.resetAllMocks()
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
      billing_subscription_manageable: true,
    })
    const onPracticeChange = renderNotice(
      "/settings?billing=success&returnTo=%2Fschedule",
      restored,
    )
    expect(screen.getByText(/subscription confirmed/i)).toBeInTheDocument()
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
      billing_subscription_manageable: true,
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
    expect(screen.getByText(/subscription confirmed/i)).toBeInTheDocument()
  })

  it("does not mistake an active platform trial for Stripe confirmation", async () => {
    vi.useFakeTimers()
    const platformTrial = practice({ entitled: true })
    const stripeTrial = practice({
      entitled: true,
      subscription_status: "trialing",
      billing_subscription_manageable: true,
    })
    vi.mocked(getPractice)
      .mockResolvedValueOnce(platformTrial)
      .mockResolvedValueOnce(stripeTrial)
    const onPracticeChange = renderNotice(
      "/settings?billing=success",
      platformTrial,
    )

    expect(
      screen.getByText(/confirming your subscription/i),
    ).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500)
    })
    expect(
      screen.queryByText(/subscription confirmed/i),
    ).not.toBeInTheDocument()
    expect(onPracticeChange).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500)
    })
    expect(getPractice).toHaveBeenCalledTimes(2)
    expect(onPracticeChange).toHaveBeenCalledWith(stripeTrial)
    expect(screen.getByText(/subscription confirmed/i)).toBeInTheDocument()
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

  it.each(["active", "trialing"])(
    "waits for Core entitlement when Stripe still reports %s",
    (subscription_status) => {
      renderNotice(
        "/settings?billing=success",
        practice({
          subscription_status,
          billing_subscription_manageable: true,
          entitled: false,
        }),
      )
      expect(
        screen.getByText(/confirming your subscription/i),
      ).toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: /continue to cosmas/i }),
      ).not.toBeInTheDocument()
    },
  )

  it.each([
    { baa_accepted: false },
    { access_authorized: false },
    { access_revoked: true },
    { access_ended: true },
  ])(
    "keeps confirmed billing separate from blocked practice access: %j",
    (blocked) => {
      renderNotice(
        "/settings?billing=success",
        practice({
          entitled: true,
          subscription_status: "active",
          billing_subscription_manageable: true,
          ...blocked,
        }),
      )
      expect(screen.getByText(/subscription confirmed/i)).toBeInTheDocument()
      expect(
        screen.queryByText(/your practice access is ready/i),
      ).not.toBeInTheDocument()
      expect(
        screen.getByText(
          /review the remaining practice access requirements below/i,
        ),
      ).toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: /continue to cosmas/i }),
      ).not.toBeInTheDocument()
    },
  )

  it("recovers from a failed status request and waits for current entitlement", async () => {
    vi.useFakeTimers()
    const unconfirmed = practice({
      subscription_status: "trialing",
      billing_subscription_manageable: true,
      entitled: false,
    })
    const restored = { ...unconfirmed, entitled: true }
    vi.mocked(getPractice)
      .mockRejectedValueOnce(new Error("Synthetic status outage"))
      .mockResolvedValueOnce(unconfirmed)
      .mockResolvedValueOnce(restored)
    const changed = renderNotice("/settings?billing=success", practice())

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000)
    })
    expect(changed).not.toHaveBeenCalled()
    expect(
      screen.queryByText(/your practice access is ready/i),
    ).not.toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500)
    })
    expect(changed).toHaveBeenCalledExactlyOnceWith(restored)
    expect(
      screen.getByText(/your practice access is ready/i),
    ).toBeInTheDocument()
  })

  it("starts a fresh bounded check after delayed confirmation", async () => {
    vi.useFakeTimers()
    vi.mocked(getPractice).mockResolvedValue(practice())
    const changed = renderNotice("/settings?billing=success", practice())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(getPractice).toHaveBeenCalledTimes(24)
    expect(changed).not.toHaveBeenCalled()

    const restored = practice({
      entitled: true,
      subscription_status: "active",
      billing_subscription_manageable: true,
    })
    vi.mocked(getPractice).mockResolvedValue(restored)
    fireEvent.click(screen.getByRole("button", { name: /check again/i }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500)
    })
    expect(getPractice).toHaveBeenCalledTimes(25)
    expect(changed).toHaveBeenCalledExactlyOnceWith(restored)
    expect(
      screen.getByText(/your practice access is ready/i),
    ).toBeInTheDocument()
  })

  it("uses the polled access requirements before offering continuation", async () => {
    vi.useFakeTimers()
    const confirmed = practice({
      entitled: true,
      subscription_status: "active",
      billing_subscription_manageable: true,
      baa_accepted: false,
    })
    vi.mocked(getPractice).mockResolvedValue(confirmed)
    const changed = renderNotice("/settings?billing=success", practice())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500)
    })
    expect(changed).toHaveBeenCalledExactlyOnceWith(confirmed)
    expect(screen.getByText(/subscription confirmed/i)).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /continue to cosmas/i }),
    ).not.toBeInTheDocument()
  })

  it("removes continuation when access changes after confirmation", () => {
    const current = practice({
      entitled: true,
      subscription_status: "active",
      billing_subscription_manageable: true,
    })
    const changed = vi.fn()
    const notice = (value: PracticeStatus) => (
      <MemoryRouter initialEntries={["/settings?billing=success"]}>
        <BillingReturnNotice practice={value} onPracticeChange={changed} />
      </MemoryRouter>
    )
    const view = render(notice(current))
    expect(
      screen.getByRole("button", { name: /continue to cosmas/i }),
    ).toBeInTheDocument()
    view.rerender(notice({ ...current, access_revoked: true }))
    expect(
      screen.queryByRole("button", { name: /continue to cosmas/i }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(
        /review the remaining practice access requirements below/i,
      ),
    ).toBeInTheDocument()
  })
})
