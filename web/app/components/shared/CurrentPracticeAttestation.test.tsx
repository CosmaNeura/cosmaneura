import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  type PracticeStatus,
  attestCurrentPracticeMember,
} from "~/lib/practice-api"

import { CurrentPracticeAttestation } from "./CurrentPracticeAttestation"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

vi.mock("~/lib/practice-api", () => ({ attestCurrentPracticeMember: vi.fn() }))

beforeEach(() => vi.resetAllMocks())

function practice(overrides: Partial<PracticeStatus> = {}): PracticeStatus {
  return {
    legal_name: "Invented current practice",
    join_code: null,
    email_domain: "synthetic.invalid",
    origin_channel: "direct",
    pastoral_pack: false,
    baa_accepted: true,
    role: "workforce",
    attested: false,
    self_attestation_available: true,
    attestation_practice_ref: "practice_00000000000000000000000001",
    attestation_baa_reference: "CN-BAA-CUST-CW v2.3",
    access_authorized: false,
    authorization_basis: null,
    access_revoked: false,
    access_ends_at: null,
    access_ended: false,
    pilot_access_grant_active: true,
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
    entitled: false,
    subscription_status: null,
    seat_summary: null,
    ...overrides,
  }
}

function fill() {
  for (const checkbox of screen.getAllByRole("checkbox"))
    fireEvent.click(checkbox)
  fireEvent.change(screen.getByLabelText("Typed name"), {
    target: { value: "Synthetic Clinician" },
  })
}

describe("CurrentPracticeAttestation", () => {
  it("requires real checks and a typed name before submitting the reviewed practice", async () => {
    const before = practice()
    const after = practice({
      attested: true,
      self_attestation_available: false,
    })
    vi.mocked(attestCurrentPracticeMember).mockResolvedValue(after)
    const onPracticeChange = vi.fn()
    render(
      <CurrentPracticeAttestation
        practice={before}
        viewAsActive={false}
        onPracticeChange={onPracticeChange}
      />,
    )
    const button = screen.getByRole("button", {
      name: "Record my workforce attestation",
    })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText("Typed name"), {
      target: { value: "Synthetic Clinician" },
    })
    expect(button).toBeDisabled()
    for (const checkbox of screen.getAllByRole("checkbox"))
      fireEvent.click(checkbox)
    fireEvent.click(button)
    await waitFor(() => expect(onPracticeChange).toHaveBeenCalledWith(after))
    expect(attestCurrentPracticeMember).toHaveBeenCalledWith({
      practice_ref: before.attestation_practice_ref,
      baa_reference: before.attestation_baa_reference,
      attestation_name: "Synthetic Clinician",
      checks: ["workforce", "not-party", "direction", "phi"],
    })
    expect(screen.queryByText(/join code/i)).not.toBeInTheDocument()
  })

  it.each([
    { self_attestation_available: undefined },
    { self_attestation_available: false },
    { attestation_practice_ref: null },
    { attestation_baa_reference: null },
    { attested: true },
    { access_revoked: true },
    { access_ended: true },
    { access_ends_at: "2026-11-07T00:00:00Z" },
    { baa_accepted: false },
  ])("hides an unavailable or stale form: %j", (change) => {
    render(
      <CurrentPracticeAttestation
        practice={practice(change)}
        viewAsActive={false}
        onPracticeChange={vi.fn()}
      />,
    )
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
    expect(attestCurrentPracticeMember).not.toHaveBeenCalled()
  })

  it("never offers attestation while viewing another account", () => {
    render(
      <CurrentPracticeAttestation
        practice={practice()}
        viewAsActive
        onPracticeChange={vi.fn()}
      />,
    )
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
    expect(attestCurrentPracticeMember).not.toHaveBeenCalled()
  })

  it("requires new acknowledgements when the displayed practice agreement changes", () => {
    const onPracticeChange = vi.fn()
    const { rerender } = render(
      <CurrentPracticeAttestation
        practice={practice()}
        viewAsActive={false}
        onPracticeChange={onPracticeChange}
      />,
    )
    fill()
    rerender(
      <CurrentPracticeAttestation
        practice={practice({
          attestation_baa_reference: "Updated synthetic agreement",
        })}
        viewAsActive={false}
        onPracticeChange={onPracticeChange}
      />,
    )
    expect(screen.getByLabelText("Typed name")).toHaveValue("")
    expect(
      screen
        .getAllByRole("checkbox")
        .every((item) => !(item as HTMLInputElement).checked),
    ).toBe(true)
    expect(
      screen.getByRole("button", { name: "Record my workforce attestation" }),
    ).toBeDisabled()
    expect(attestCurrentPracticeMember).not.toHaveBeenCalled()
  })

  it("keeps a refused submission editable without claiming access", async () => {
    vi.mocked(attestCurrentPracticeMember).mockRejectedValue(
      new Error("The practice agreement changed. Review it again."),
    )
    const changed = vi.fn()
    render(
      <CurrentPracticeAttestation
        practice={practice()}
        viewAsActive={false}
        onPracticeChange={changed}
      />,
    )
    fill()
    fireEvent.click(
      screen.getByRole("button", { name: "Record my workforce attestation" }),
    )
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Review it again.",
    )
    expect(changed).not.toHaveBeenCalled()
    expect(
      screen.getByRole("button", { name: "Record my workforce attestation" }),
    ).toBeEnabled()
  })

  it("disables the submit control while the real request is pending", () => {
    vi.mocked(attestCurrentPracticeMember).mockReturnValue(
      new Promise(() => {}),
    )
    render(
      <CurrentPracticeAttestation
        practice={practice()}
        viewAsActive={false}
        onPracticeChange={vi.fn()}
      />,
    )
    fill()
    fireEvent.click(
      screen.getByRole("button", { name: "Record my workforce attestation" }),
    )
    expect(screen.getByRole("button", { name: "Recording…" })).toBeDisabled()
  })
})
