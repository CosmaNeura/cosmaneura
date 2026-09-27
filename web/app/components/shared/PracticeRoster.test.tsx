import { beforeEach, describe, expect, it, vi } from "vitest"

import type { PracticeRosterMember } from "~/configuration/client"
import type { BillingSeatSummary, PracticeStatus } from "~/lib/practice-api"

import { PracticeRoster, loadPracticeRoster } from "./PracticeRoster"
import { render, screen } from "@testing-library/react"

const { practiceRoster } = vi.hoisted(() => ({ practiceRoster: vi.fn() }))
vi.mock("~/configuration/client", () => ({ practiceRoster }))

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

function seatSummary(
  account_kind: BillingSeatSummary["account_kind"],
): BillingSeatSummary {
  return {
    account_kind,
    active_clinicians: 1,
    billed_seats: 1,
    seat_sync_pending: false,
    unit_amount: 8900,
    currency: "usd",
    interval: "month",
  }
}

let memberSequence = 0

function member(
  overrides: Partial<PracticeRosterMember> = {},
): PracticeRosterMember {
  memberSequence += 1
  return {
    member_id: `practice_member_${memberSequence}`,
    display_name: "Pat Owner",
    role: "owner",
    is_self: true,
    seat_active: true,
    revoked: false,
    access_in_force: true,
    joined_at: "2026-08-20T20:00:00Z",
    ...overrides,
  }
}

const TEAM: PracticeRosterMember[] = [
  member(),
  member({ display_name: "Alex Active", role: "workforce", is_self: false }),
  member({
    display_name: "Nora NoAccess",
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
    display_name: "Rae Revoked",
    role: "workforce",
    is_self: false,
    seat_active: false,
    revoked: true,
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
    expect(rows).toHaveLength(6)
    expect(rows[0]).toHaveTextContent(
      /^Pat Owner \(you\) · Owner\s*Active · joined/,
    )
    expect(rows[1]).toHaveTextContent(
      /^Alex Active · Clinician\s*Active · joined/,
    )
    expect(rows[2]).toHaveTextContent(
      /^Nora NoAccess · Clinician\s*No access · joined/,
    )
    expect(rows[3]).toHaveTextContent(
      /^Ian Inactive · Clinician\s*Inactive · joined/,
    )
    expect(rows[4]).toHaveTextContent(
      /^Rae Revoked · Clinician\s*Revoked · joined/,
    )
    expect(rows[5]).toHaveTextContent(/^Name not recorded · Clinician\s*Active/)
    expect(screen.queryByText(/only you so far/i)).not.toBeInTheDocument()
  })

  it("says so when the owner is the only member of a practice", () => {
    render(
      <PracticeRoster
        practice={practice({ seat_summary: seatSummary("practice") })}
        members={[member()]}
      />,
    )

    expect(screen.getByText("Only you so far.")).toBeInTheDocument()
  })

  it("does not suggest a solo account will gain members", () => {
    render(
      <PracticeRoster
        practice={practice({ seat_summary: seatSummary("solo") })}
        members={[member()]}
      />,
    )

    expect(screen.getAllByRole("listitem")).toHaveLength(1)
    expect(screen.queryByText(/only you so far/i)).not.toBeInTheDocument()
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

  it("renders nothing when there is no roster to show", () => {
    const { container } = render(
      <PracticeRoster practice={practice()} members={null} />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it("says so when the roster failed to load", () => {
    render(<PracticeRoster practice={practice()} members="unavailable" />)

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Practice members could not be loaded. Refresh to try again.",
    )
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument()
  })

  it("does not suggest refreshing when the owner's access is restricted", () => {
    render(<PracticeRoster practice={practice()} members="restricted" />)

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Practice members are not available while your access is restricted.",
    )
    expect(screen.queryByText(/refresh/i)).not.toBeInTheDocument()
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument()
  })
})

describe("loadPracticeRoster", () => {
  beforeEach(() => {
    practiceRoster.mockReset()
  })

  it("returns the members", async () => {
    practiceRoster.mockResolvedValue({
      data: { members: TEAM },
      response: new Response(null, { status: 200 }),
    })

    await expect(loadPracticeRoster()).resolves.toEqual(TEAM)
  })

  it("shows nothing while the practice is lapsed", async () => {
    practiceRoster.mockResolvedValue({
      data: undefined,
      response: new Response(null, { status: 402 }),
    })

    await expect(loadPracticeRoster()).resolves.toBeNull()
  })

  it("reports restricted access when the owner is refused", async () => {
    practiceRoster.mockResolvedValue({
      data: undefined,
      response: new Response(null, { status: 403 }),
    })

    await expect(loadPracticeRoster()).resolves.toBe("restricted")
  })

  it("reports a server failure instead of hiding it", async () => {
    practiceRoster.mockResolvedValue({
      data: undefined,
      response: new Response(null, { status: 500 }),
    })

    await expect(loadPracticeRoster()).resolves.toBe("unavailable")
  })

  it("reports a network failure instead of hiding it", async () => {
    practiceRoster.mockRejectedValue(new TypeError("Failed to fetch"))

    await expect(loadPracticeRoster()).resolves.toBe("unavailable")
  })
})
