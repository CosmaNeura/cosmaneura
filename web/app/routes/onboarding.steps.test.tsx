import { beforeEach, describe, expect, it, vi } from "vitest"

import type { PracticeStatus } from "~/lib/practice-api"

import { AllSetStep, BiographyStep } from "./onboarding"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const client = vi.hoisted(() => ({
  updateSettingsData: vi.fn(),
  completeOnboarding: vi.fn(),
}))
vi.mock("~/configuration/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/configuration/client")>()),
  ...client,
}))

const toast = vi.hoisted(() => vi.fn())
vi.mock("~/hooks/use-toast", () => ({ toast }))
vi.mock("~/components/shared/PracticeJoinPanel", () => ({
  PracticeJoinPanel: () => null,
}))

function practice(overrides: Partial<PracticeStatus> = {}): PracticeStatus {
  return {
    legal_name: "Pilot Organization",
    join_code: "PILOT123",
    email_domain: "example.com",
    origin_channel: "direct",
    pastoral_pack: false,
    baa_accepted: true,
    role: "owner",
    attested: false,
    access_authorized: true,
    authorization_basis: "organization_roster",
    access_revoked: false,
    access_ends_at: null,
    access_ended: false,
    pilot_access_grant_active: false,
    trial_ends_at: null,
    billing_exempt: true,
    billing_exempt_until: null,
    billing_exempt_active: true,
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

beforeEach(() => {
  window.history.replaceState({}, "", "/onboarding")
  client.updateSettingsData.mockReset()
  client.completeOnboarding.mockReset()
  toast.mockReset()
})

function fillBiography(text: string) {
  fireEvent.change(screen.getByLabelText("Biography"), {
    target: { value: text },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save & Continue" }))
}

describe("onboarding biography step", () => {
  it("advances once the biography is saved", async () => {
    client.updateSettingsData.mockResolvedValue({ data: {}, error: undefined })
    const onComplete = vi.fn()
    render(
      <BiographyStep
        biography={null}
        onComplete={onComplete}
        onSkip={vi.fn()}
      />,
    )

    fillBiography("Synthetic biography")

    await waitFor(() => expect(onComplete).toHaveBeenCalledOnce())
    expect(client.updateSettingsData).toHaveBeenCalledWith({
      body: { biography: "Synthetic biography" },
    })
  })

  it.each([
    ["the server rejects it", { data: undefined, error: { detail: "x" } }],
  ])("stays on the step with the text intact when %s", async (_, result) => {
    client.updateSettingsData.mockResolvedValue(result)
    const onComplete = vi.fn()
    render(
      <BiographyStep
        biography={null}
        onComplete={onComplete}
        onSkip={vi.fn()}
      />,
    )

    fillBiography("Synthetic biography")

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Couldn't save your biography",
          variant: "destructive",
        }),
      ),
    )
    expect(onComplete).not.toHaveBeenCalled()
    expect(screen.getByLabelText("Biography")).toHaveValue(
      "Synthetic biography",
    )
    expect(
      screen.getByRole("button", { name: "Save & Continue" }),
    ).toBeEnabled()
  })
})

describe("onboarding final step", () => {
  it("routes an owner with paused billing to recovery without claiming readiness", async () => {
    client.completeOnboarding.mockResolvedValue({
      data: null,
      error: undefined,
    })
    render(
      <AllSetStep
        practice={practice({
          entitled: false,
          billing_exempt: false,
          billing_exempt_active: false,
        })}
      />,
    )
    expect(
      screen.getByRole("heading", { name: "Your practice setup is saved" }),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Cosmas is ready/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Review billing" }))
    await waitFor(() => expect(window.location.pathname).toBe("/settings"))
    expect(new URLSearchParams(window.location.search).get("billing")).toBe(
      "required",
    )
    expect(new URLSearchParams(window.location.search).get("returnTo")).toBe(
      "/notes",
    )
    expect(client.completeOnboarding).toHaveBeenCalledOnce()
  })

  it("directs an unentitled workforce member to their practice owner", async () => {
    render(
      <AllSetStep
        practice={practice({ entitled: false, role: "workforce" })}
      />,
    )
    expect(
      screen.getByText(/practice owner needs to restore billing/),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Review practice access" }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Review billing" }),
    ).not.toBeInTheDocument()
  })

  it("keeps server-confirmed pilot access ready without a practice subscription", async () => {
    render(
      <AllSetStep
        practice={practice({
          entitled: false,
          pilot_access_grant_active: true,
        })}
      />,
    )
    expect(
      screen.getByRole("heading", { name: "You're all set!" }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Go to Cosmas" }),
    ).toBeInTheDocument()
  })

  it("goes to notes once setup is complete", async () => {
    client.completeOnboarding.mockResolvedValue({
      data: null,
      error: undefined,
    })
    render(<AllSetStep practice={practice()} />)

    fireEvent.click(screen.getByRole("button", { name: "Go to Cosmas" }))

    await waitFor(() => expect(window.location.pathname).toBe("/notes"))
  })

  it("shows the backend's explanation for a practice-access problem", async () => {
    client.completeOnboarding.mockResolvedValue({
      data: undefined,
      error: {
        detail:
          "Name or join a practice and accept the BAA before using Cosmas.",
      },
    })
    render(<AllSetStep practice={practice()} />)

    fireEvent.click(screen.getByRole("button", { name: "Go to Cosmas" }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          description:
            "Name or join a practice and accept the BAA before using Cosmas.",
        }),
      ),
    )
  })

  it("stays put and lets the user retry when finishing setup fails", async () => {
    client.completeOnboarding.mockResolvedValue({
      data: undefined,
      error: { detail: "x" },
    })
    const start = window.location.href
    render(<AllSetStep practice={practice()} />)

    fireEvent.click(screen.getByRole("button", { name: "Go to Cosmas" }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Couldn't finish setup" }),
      ),
    )
    expect(window.location.href).toBe(start)
    expect(screen.getByRole("button", { name: "Go to Cosmas" })).toBeEnabled()
  })
})
