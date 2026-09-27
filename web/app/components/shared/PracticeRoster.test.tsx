import { beforeEach, describe, expect, it, vi } from "vitest"

import type {
  PracticeInvitationIssued,
  PracticeRosterInvitation,
  PracticeRosterMember,
  PracticeRoster as Roster,
} from "~/configuration/client"
import type { BillingSeatSummary, PracticeStatus } from "~/lib/practice-api"

import { PracticeRoster, loadPracticeRoster } from "./PracticeRoster"
import { fireEvent, render, screen, within } from "@testing-library/react"

const client = vi.hoisted(() => ({
  practiceRoster: vi.fn(),
  createPracticeInvitation: vi.fn(),
  resendPracticeInvitation: vi.fn(),
  revokePracticeInvitation: vi.fn(),
}))
vi.mock("~/configuration/client", () => client)
const { practiceRoster } = client

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

function roster(
  members: PracticeRosterMember[],
  overrides: Partial<Roster> = {},
): Roster {
  return {
    members,
    invitations: [],
    occupied_seats: members.filter((m) => m.seat_active).length,
    ...overrides,
  }
}

function invitation(
  overrides: Partial<PracticeRosterInvitation> = {},
): PracticeRosterInvitation {
  return {
    invitation_id: "practice_invitation_live",
    invitee_email: "riley@gmail.com",
    issued_at: "2026-09-20T12:00:00Z",
    expires_at: "2026-09-27T12:00:00Z",
    expired: false,
    ...overrides,
  }
}

function issued(
  overrides: Partial<PracticeInvitationIssued> = {},
): PracticeInvitationIssued {
  return {
    invitation: invitation({ invitee_email: "new@gmail.com" }),
    invite_url: "https://app.cosmaneura.com/invite#t=secret-token",
    email_queued: false,
    ...overrides,
  }
}

describe("PracticeRoster", () => {
  it("lists each member's name, role, and access state for the owner", () => {
    render(<PracticeRoster practice={practice()} roster={roster(TEAM)} />)

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
        roster={roster([member()])}
      />,
    )

    expect(screen.getByText("Only you so far.")).toBeInTheDocument()
  })

  it("does not suggest a solo account will gain members", () => {
    render(
      <PracticeRoster
        practice={practice({ seat_summary: seatSummary("solo") })}
        roster={roster([member()])}
      />,
    )

    expect(screen.getAllByRole("listitem")).toHaveLength(1)
    expect(screen.queryByText(/only you so far/i)).not.toBeInTheDocument()
  })

  it("lists pending and expired invitations beside the occupied seats", () => {
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member()], {
          occupied_seats: 1,
          invitations: [
            invitation(),
            invitation({
              invitation_id: "practice_invitation_stale",
              invitee_email: "stale@gmail.com",
              expired: true,
            }),
          ],
        })}
      />,
    )

    expect(
      screen.getByText(
        "1 seat in use · 1 invitation pending — not billed until accepted",
      ),
    ).toBeInTheDocument()
    const rows = screen.getAllByRole("listitem")
    expect(rows[1]).toHaveTextContent(/^riley@gmail.com · Invited · expires/)
    expect(within(rows[1]).getByRole("button", { name: "Revoke" })).toBeTruthy()
    expect(within(rows[1]).getByRole("button", { name: "Resend" })).toBeTruthy()
    expect(rows[2]).toHaveTextContent(/^stale@gmail.com · Expired/)
    expect(within(rows[2]).getByRole("button", { name: "Remove" })).toBeTruthy()
    expect(within(rows[2]).getByRole("button", { name: "Resend" })).toBeTruthy()
    expect(screen.queryByText(/of 20/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/only you so far/i)).not.toBeInTheDocument()
  })

  it("labels an invitation whose address is withheld", () => {
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member()], {
          invitations: [invitation({ invitee_email: null })],
        })}
      />,
    )

    expect(screen.getByText("Invited clinician")).toBeInTheDocument()
  })

  it("revokes an invitation and reloads the roster", async () => {
    client.revokePracticeInvitation.mockResolvedValue({ data: undefined })
    practiceRoster.mockResolvedValue({
      data: roster([member()]),
      response: new Response(null, { status: 200 }),
    })
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member()], { invitations: [invitation()] })}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke" }))

    await vi.waitFor(() =>
      expect(screen.queryByText("riley@gmail.com")).not.toBeInTheDocument(),
    )
    expect(client.revokePracticeInvitation).toHaveBeenCalledWith({
      path: { invitation_id: "practice_invitation_live" },
    })
  })

  it("resends an invitation with a fresh key and shows its link", async () => {
    client.resendPracticeInvitation.mockResolvedValue({
      data: issued({ invitation: invitation() }),
    })
    practiceRoster.mockResolvedValue({
      data: roster([member()], { invitations: [invitation()] }),
      response: new Response(null, { status: 200 }),
    })
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member()], { invitations: [invitation()] })}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Resend" }))

    expect(
      await screen.findByText("Send this link to riley@gmail.com."),
    ).toBeInTheDocument()
    const [options] = client.resendPracticeInvitation.mock.calls[0]
    expect(options.path).toEqual({ invitation_id: "practice_invitation_live" })
    expect(options.headers["Idempotency-Key"]).toMatch(/.{8,}/)
  })

  it("shows the reason a revoke was refused", async () => {
    client.revokePracticeInvitation.mockResolvedValue({
      error: { detail: "An accepted invitation cannot be revoked." },
    })
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member()], { invitations: [invitation()] })}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "An accepted invitation cannot be revoked.",
    )
  })

  it("renders nothing for a workforce member", () => {
    const { container } = render(
      <PracticeRoster
        practice={practice({ role: "workforce" })}
        roster={roster(TEAM)}
      />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing when there is no roster to show", () => {
    const { container } = render(
      <PracticeRoster practice={practice()} roster={null} />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it("says so when the roster failed to load", () => {
    render(<PracticeRoster practice={practice()} roster="unavailable" />)

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Practice members could not be loaded. Refresh to try again.",
    )
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument()
  })

  it("does not suggest refreshing when the owner's access is restricted", () => {
    render(<PracticeRoster practice={practice()} roster="restricted" />)

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

  it("returns the roster", async () => {
    practiceRoster.mockResolvedValue({
      data: roster(TEAM),
      response: new Response(null, { status: 200 }),
    })

    await expect(loadPracticeRoster()).resolves.toEqual(roster(TEAM))
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

describe("InviteClinicianForm", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    practiceRoster.mockResolvedValue({
      data: roster([member()], { invitations: [invitation()] }),
      response: new Response(null, { status: 200 }),
    })
  })

  function invite(email: string) {
    render(<PracticeRoster practice={practice()} roster={roster([member()])} />)
    fireEvent.change(screen.getByLabelText("Invite a clinician"), {
      target: { value: email },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }))
  }

  it("gives the owner a copyable link to send", async () => {
    client.createPracticeInvitation.mockResolvedValue({ data: issued() })

    invite("new@gmail.com")

    expect(
      await screen.findByText("Send this link to new@gmail.com."),
    ).toBeInTheDocument()
    expect(
      screen.getByText("https://app.cosmaneura.com/invite#t=secret-token"),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Copy to Clipboard" }),
    ).toBeInTheDocument()
    const [options] = client.createPracticeInvitation.mock.calls[0]
    expect(options.body).toEqual({ email: "new@gmail.com" })
    expect(await screen.findByText("riley@gmail.com")).toBeInTheDocument()
  })

  it("says the email was sent when it was queued", async () => {
    client.createPracticeInvitation.mockResolvedValue({
      data: issued({ email_queued: true }),
    })

    invite("new@gmail.com")

    expect(
      await screen.findByText("Email sent. You can also share this link."),
    ).toBeInTheDocument()
  })

  it("uses a fresh idempotency key for every submit", async () => {
    client.createPracticeInvitation.mockResolvedValue({ data: issued() })

    invite("one@gmail.com")
    await screen.findByText("Send this link to new@gmail.com.")
    fireEvent.change(screen.getByLabelText("Invite a clinician"), {
      target: { value: "two@gmail.com" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }))
    await vi.waitFor(() =>
      expect(client.createPracticeInvitation).toHaveBeenCalledTimes(2),
    )

    const keys = client.createPracticeInvitation.mock.calls.map(
      ([options]) => options.headers["Idempotency-Key"],
    )
    expect(new Set(keys).size).toBe(2)
  })

  it("passes on the contact-us wording at the seat cap", async () => {
    client.createPracticeInvitation.mockResolvedValue({
      error: {
        detail: "To add more clinicians, contact us at info@cosmaneura.com.",
      },
    })

    invite("full@gmail.com")

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "To add more clinicians, contact us at info@cosmaneura.com.",
    )
    expect(screen.queryByText(/of 20/i)).not.toBeInTheDocument()
  })
})
