import { describe, expect, it } from "vitest"

import type { PracticeRosterMember } from "~/configuration/client"
import type { PracticeStatus } from "~/lib/practice-api"

import { PracticeRoster } from "./PracticeRoster"
import { render, screen } from "@testing-library/react"

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
    trial_ends_at: null,
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

function member(
  overrides: Partial<PracticeRosterMember> = {},
): PracticeRosterMember {
  return {
    display_name: "Pat Owner",
    role: "owner",
    is_self: true,
    seat_active: true,
    access_in_force: true,
    joined_at: "2026-08-20T20:00:00Z",
    ...overrides,
  }
}

const TEAM: PracticeRosterMember[] = [
  member(),
  member({ display_name: "Alex Active", role: "workforce", is_self: false }),
  member({
    display_name: "Paige Paused",
    role: "workforce",
    is_self: false,
    access_in_force: false,
  }),
  member({
    display_name: "Ian Inactive",
    role: "workforce",
    is_self: false,
    seat_active: false,
    access_in_force: false,
  }),
  member({
    display_name: null,
    role: "workforce",
    is_self: false,
  }),
]

describe("PracticeRoster", () => {
  it("lists each member's name, role, and access state for the owner", () => {
    render(<PracticeRoster practice={practice()} members={TEAM} />)

    const rows = screen.getAllByRole("listitem")
    expect(rows).toHaveLength(5)
    expect(rows[0]).toHaveTextContent(
      /^Pat Owner \(you\) · Owner\s*Active · joined/,
    )
    expect(rows[1]).toHaveTextContent(
      /^Alex Active · Clinician\s*Active · joined/,
    )
    expect(rows[2]).toHaveTextContent(
      /^Paige Paused · Clinician\s*Access paused · joined/,
    )
    expect(rows[3]).toHaveTextContent(
      /^Ian Inactive · Clinician\s*Inactive · joined/,
    )
    expect(rows[4]).toHaveTextContent(/^Name not recorded · Clinician\s*Active/)
    expect(screen.queryByText(/only you so far/i)).not.toBeInTheDocument()
  })

  it("says so when the owner is the only member", () => {
    render(<PracticeRoster practice={practice()} members={[member()]} />)

    expect(screen.getByText("Only you so far.")).toBeInTheDocument()
  })

  it("offers no invite or remove actions yet", () => {
    render(<PracticeRoster practice={practice()} members={TEAM} />)

    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("renders nothing for a workforce member", () => {
    const { container } = render(
      <PracticeRoster
        practice={practice({ role: "workforce" })}
        members={TEAM}
      />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing when the roster could not be loaded", () => {
    const { container } = render(
      <PracticeRoster practice={practice()} members={null} />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})
