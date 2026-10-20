import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type {
  PracticeInvitationIssued,
  PracticeRosterInvitation,
  PracticeRosterMember,
  PracticeRoster as Roster,
} from "~/configuration/client"
import type { BillingSeatSummary, PracticeStatus } from "~/lib/practice-api"

import { localAccessEnd } from "./PracticeMemberAccess"
import { PracticeRoster, loadPracticeRoster } from "./PracticeRoster"
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"

const client = vi.hoisted(() => ({
  practiceRoster: vi.fn(),
  createPracticeInvitation: vi.fn(),
  resendPracticeInvitation: vi.fn(),
  revokePracticeInvitation: vi.fn(),
  revokePracticeMember: vi.fn(),
  restorePracticeMember: vi.fn(),
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
    access_ends_at: null,
    access_ended: false,
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
    access_ends_at: null,
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
  beforeEach(() => {
    vi.clearAllMocks()
  })

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
    expect(
      within(rows[1]).getByRole("button", {
        name: "Revoke invitation to riley@gmail.com",
      }),
    ).toHaveTextContent("Revoke")
    expect(
      within(rows[1]).getByRole("button", {
        name: "Resend invitation to riley@gmail.com",
      }),
    ).toHaveTextContent("Resend")
    expect(rows[2]).toHaveTextContent(/^stale@gmail.com · Expired/)
    expect(
      within(rows[2]).getByRole("button", {
        name: "Remove invitation to stale@gmail.com",
      }),
    ).toHaveTextContent("Remove")
    expect(
      within(rows[2]).getByRole("button", {
        name: "Resend invitation to stale@gmail.com",
      }),
    ).toHaveTextContent("Resend")
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
    expect(
      screen.getByRole("button", {
        name: "Revoke invitation to invited clinician",
      }),
    ).toBeInTheDocument()
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

    fireEvent.click(screen.getByRole("button", { name: /^Revoke invitation/ }))

    await waitFor(() =>
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

    fireEvent.click(screen.getByRole("button", { name: /^Resend invitation/ }))

    expect(
      await screen.findByText("Send this link to riley@gmail.com."),
    ).toBeInTheDocument()
    const [options] = client.resendPracticeInvitation.mock.calls[0]
    expect(options.path).toEqual({ invitation_id: "practice_invitation_live" })
    expect(options.headers["Idempotency-Key"]).toMatch(/.{8,}/)
  })

  it("shows the reason a revoke was refused and reloads the roster", async () => {
    client.revokePracticeInvitation.mockResolvedValue({
      error: { detail: "An accepted invitation cannot be revoked." },
      response: new Response(null, { status: 409 }),
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

    fireEvent.click(screen.getByRole("button", { name: /^Revoke invitation/ }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "An accepted invitation cannot be revoked.",
    )
    await waitFor(() => expect(practiceRoster).toHaveBeenCalledTimes(1))
  })

  it("drops the copyable link once its invitation is revoked", async () => {
    client.resendPracticeInvitation.mockResolvedValue({
      data: issued({ invitation: invitation() }),
    })
    client.revokePracticeInvitation.mockResolvedValue({ data: undefined })
    practiceRoster
      .mockResolvedValueOnce({
        data: roster([member()], { invitations: [invitation()] }),
        response: new Response(null, { status: 200 }),
      })
      .mockResolvedValueOnce({
        data: roster([member()]),
        response: new Response(null, { status: 200 }),
      })
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member()], { invitations: [invitation()] })}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: /^Resend invitation/ }))
    expect(
      await screen.findByText(
        "https://app.cosmaneura.com/invite#t=secret-token",
      ),
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /^Revoke invitation/ }))

    await waitFor(() =>
      expect(
        screen.queryByText("https://app.cosmaneura.com/invite#t=secret-token"),
      ).not.toBeInTheDocument(),
    )
    expect(screen.getByRole("status")).toBeEmptyDOMElement()
  })

  it("uses a new resend key per click, and the same key to retry a lost one", async () => {
    client.resendPracticeInvitation
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue({ data: issued({ invitation: invitation() }) })
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
    const resend = () =>
      fireEvent.click(
        screen.getByRole("button", { name: /^Resend invitation/ }),
      )

    resend()
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That did not work. Try again.",
    )
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /^Resend invitation/ }),
      ).toBeEnabled(),
    )
    resend()
    await screen.findByText("Send this link to riley@gmail.com.")
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /^Resend invitation/ }),
      ).toBeEnabled(),
    )
    resend()
    await waitFor(() =>
      expect(client.resendPracticeInvitation).toHaveBeenCalledTimes(3),
    )

    const [lost, retried, again] =
      client.resendPracticeInvitation.mock.calls.map(
        ([options]) => options.headers["Idempotency-Key"],
      )
    expect(retried).toBe(lost)
    expect(again).not.toBe(retried)
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

describe("revoking and restoring members", () => {
  const ENDS_AT = "2026-09-28T04:00:00Z"

  beforeEach(() => {
    vi.clearAllMocks()
    practiceRoster.mockResolvedValue({
      data: roster([member()]),
      response: new Response(null, { status: 200 }),
    })
  })

  function clinician(overrides: Partial<PracticeRosterMember> = {}) {
    return member({
      member_id: "practice_member_alex",
      display_name: "Alex Active",
      role: "workforce",
      is_self: false,
      ...overrides,
    })
  }

  it("offers revoke only for other active clinicians", () => {
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([
          member(),
          clinician(),
          clinician({
            member_id: "practice_member_rae",
            display_name: "Rae Revoked",
            seat_active: false,
            revoked: true,
            access_in_force: false,
            // support closed the account after the owner had revoked it
            access_ends_at: ENDS_AT,
          }),
        ])}
      />,
    )

    const rows = screen.getAllByRole("listitem")
    expect(within(rows[0]).queryByRole("button")).not.toBeInTheDocument()
    expect(
      within(rows[1]).getByRole("button", { name: "Revoke Alex Active" }),
    ).toBeInTheDocument()
    expect(rows[2]).toHaveTextContent(/Revoked · joined/)
    expect(within(rows[2]).queryByRole("button")).not.toBeInTheDocument()
  })

  it("never offers the owner's own row or a co-owner an action", () => {
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([
          member(),
          clinician({
            member_id: "practice_member_co_owner",
            display_name: "Casey CoOwner",
            role: "owner",
          }),
        ])}
      />,
    )

    const rows = screen.getAllByRole("listitem")
    expect(rows[1]).toHaveTextContent("Casey CoOwner")
    expect(within(rows[1]).queryByRole("button")).not.toBeInTheDocument()
  })

  it("never offers a clinician an action on their own row", () => {
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([
          member({ is_self: false }),
          clinician({ is_self: true }),
        ])}
      />,
    )

    const rows = screen.getAllByRole("listitem")
    expect(rows[1]).toHaveTextContent("Alex Active (you)")
    expect(within(rows[1]).queryByRole("button")).not.toBeInTheDocument()
  })

  it("offers nothing for an inactive member the owner never revoked", () => {
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([
          member(),
          clinician({ seat_active: false, access_in_force: false }),
        ])}
      />,
    )

    const row = screen.getAllByRole("listitem")[1]
    expect(row).toHaveTextContent(/Inactive · joined/)
    expect(within(row).queryByRole("button")).not.toBeInTheDocument()
  })

  it("names the clinician in the confirmation", async () => {
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician()])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke Alex Active" }))

    expect(
      await screen.findByRole("dialog", {
        name: "Revoke Alex Active’s access?",
      }),
    ).toBeInTheDocument()
  })

  it("explains the midnight cut-off before revoking, then reloads", async () => {
    client.revokePracticeMember.mockResolvedValue({ data: clinician() })
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician()])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke Alex Active" }))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent(
      "They keep access until midnight Eastern tonight. Their seat frees " +
        "then. No credit for the rest of this billing period; your bill " +
        "drops at renewal. You can restore them anytime.",
    )
    expect(client.revokePracticeMember).not.toHaveBeenCalled()
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Revoke access" }),
    )

    await waitFor(() => expect(practiceRoster).toHaveBeenCalled())
    expect(client.revokePracticeMember).toHaveBeenCalledWith({
      path: { member_id: "practice_member_alex" },
    })
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    )
  })

  it("opens the confirmation from its trigger and returns focus to it", async () => {
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician()])}
      />,
    )

    const trigger = screen.getByRole("button", { name: "Revoke Alex Active" })
    expect(trigger).toHaveAttribute("aria-haspopup", "dialog")
    expect(trigger).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(trigger)
    const dialog = await screen.findByRole("dialog")
    expect(trigger).toHaveAttribute("aria-expanded", "true")
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it("moves focus to Restore once a revoke succeeds, and back after a restore", async () => {
    client.revokePracticeMember.mockResolvedValue({ data: clinician() })
    client.restorePracticeMember.mockResolvedValue({ data: clinician() })
    const loaded = (overrides: Partial<PracticeRosterMember>) => ({
      data: roster([member(), clinician(overrides)]),
      response: new Response(null, { status: 200 }),
    })
    practiceRoster
      .mockResolvedValueOnce(loaded({ access_ends_at: ENDS_AT }))
      .mockResolvedValueOnce(loaded({}))
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician()])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke Alex Active" }))
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Revoke access",
      }),
    )
    const restore = await screen.findByRole("button", {
      name: "Restore Alex Active",
    })
    await waitFor(() => expect(restore).toHaveFocus())

    fireEvent.click(restore)
    const revoke = await screen.findByRole("button", {
      name: "Revoke Alex Active",
    })
    await waitFor(() => expect(revoke).toHaveFocus())
  })

  it("tells apart clinicians who never recorded a name", () => {
    const joined = ["2026-08-20T20:00:00Z", "2026-09-02T20:00:00Z"]
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([
          member(),
          clinician({
            member_id: "practice_member_a",
            display_name: null,
            joined_at: joined[0],
          }),
          clinician({
            member_id: "practice_member_b",
            display_name: null,
            joined_at: joined[1],
          }),
        ])}
      />,
    )

    for (const at of joined) {
      expect(
        screen.getByRole("button", {
          name: `Revoke clinician joined ${new Date(at).toLocaleDateString()}`,
        }),
      ).toBeInTheDocument()
    }
  })

  it("holds the confirmation open and disabled while a revoke is in flight", async () => {
    client.revokePracticeMember.mockReturnValue(new Promise(() => {}))
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician()])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke Alex Active" }))
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Revoke access" }),
    )

    const pending = await within(dialog).findByRole("button", {
      name: "Revoking…",
    })
    expect(pending).toBeDisabled()
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled()
    fireEvent.keyDown(dialog, { key: "Escape" })
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })

  it("disables Restore while a restore is in flight", async () => {
    client.restorePracticeMember.mockReturnValue(new Promise(() => {}))
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician({ access_ends_at: ENDS_AT })])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Restore Alex Active" }))

    const pending = await screen.findByRole("button", {
      name: "Restoring Alex Active",
    })
    expect(pending).toBeDisabled()
    expect(pending).toHaveTextContent("Restoring…")
  })

  it("clears an earlier refusal when the confirmation is opened again", async () => {
    client.revokePracticeMember.mockResolvedValue({
      error: { detail: "That did not work. Try again." },
    })
    practiceRoster.mockResolvedValue({
      data: roster([member(), clinician()]),
      response: new Response(null, { status: 200 }),
    })
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician()])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke Alex Active" }))
    let dialog = await screen.findByRole("dialog")
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Revoke access" }),
    )
    await within(dialog).findByRole("alert")
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }))
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke Alex Active" }))
    dialog = await screen.findByRole("dialog")
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("cancelling the confirmation revokes nothing", async () => {
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician()])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke Alex Active" }))
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    )
    expect(client.revokePracticeMember).not.toHaveBeenCalled()
  })

  it("shows a clinician whose access ends tonight and the seat it frees", () => {
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician({ access_ends_at: ENDS_AT })])}
      />,
    )

    expect(
      screen.getByText("2 seats in use · 1 seat frees at midnight ET"),
    ).toBeInTheDocument()
    const row = screen.getAllByRole("listitem")[1]
    expect(row).toHaveTextContent(
      "Access ends tonight at midnight ET " +
        `(${localAccessEnd(ENDS_AT)} your time) · joined`,
    )
    expect(
      within(row).getByRole("button", { name: "Restore Alex Active" }),
    ).toBeInTheDocument()
    expect(
      within(row).queryByRole("button", { name: /revoke/i }),
    ).not.toBeInTheDocument()
  })

  it("restores a clinician after the cut-off and reloads", async () => {
    client.restorePracticeMember.mockResolvedValue({ data: clinician() })
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([
          member(),
          clinician({
            access_ends_at: ENDS_AT,
            seat_active: false,
            access_in_force: false,
          }),
        ])}
      />,
    )

    const row = screen.getAllByRole("listitem")[1]
    expect(row).toHaveTextContent(/Access revoked · joined/)
    fireEvent.click(
      within(row).getByRole("button", { name: "Restore Alex Active" }),
    )

    await waitFor(() => expect(practiceRoster).toHaveBeenCalled())
    expect(client.restorePracticeMember).toHaveBeenCalledWith({
      path: { member_id: "practice_member_alex" },
    })
  })

  it("passes on the contact-us wording when a restore meets the seat cap", async () => {
    client.restorePracticeMember.mockResolvedValue({
      error: {
        detail: "To add more clinicians, contact us at info@cosmaneura.com.",
      },
    })
    const ended = roster([
      member(),
      clinician({
        access_ends_at: ENDS_AT,
        seat_active: false,
        access_in_force: false,
      }),
    ])
    practiceRoster.mockResolvedValue({
      data: ended,
      response: new Response(null, { status: 200 }),
    })
    render(<PracticeRoster practice={practice()} roster={ended} />)

    fireEvent.click(screen.getByRole("button", { name: "Restore Alex Active" }))

    await waitFor(() => expect(practiceRoster).toHaveBeenCalled())
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "To add more clinicians, contact us at info@cosmaneura.com.",
    )
  })

  it("keeps a refused restore's reason on a row that no longer has actions", async () => {
    client.restorePracticeMember.mockResolvedValue({
      error: { detail: "This clinician's access can't be restored." },
    })
    practiceRoster.mockResolvedValue({
      data: roster([
        member(),
        clinician({
          access_ends_at: ENDS_AT,
          seat_active: false,
          revoked: true,
          access_in_force: false,
        }),
      ]),
      response: new Response(null, { status: 200 }),
    })
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([
          member(),
          clinician({
            access_ends_at: ENDS_AT,
            seat_active: false,
            access_in_force: false,
          }),
        ])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Restore Alex Active" }))

    const row = screen.getAllByRole("listitem")[1]
    await waitFor(() =>
      expect(within(row).getByRole("alert")).toHaveTextContent(
        "This clinician's access can't be restored.",
      ),
    )
    await waitFor(() => expect(row).toHaveTextContent(/Revoked · joined/))
    expect(within(row).queryByRole("button")).not.toBeInTheDocument()
    expect(within(row).getByRole("alert")).toBeInTheDocument()
  })

  it("does not reopen an abandoned confirmation when the member turns active again", async () => {
    const refusal = {
      error: { detail: "Someone else changed this clinician." },
    }
    client.revokePracticeMember.mockResolvedValue(refusal)
    client.restorePracticeMember.mockResolvedValue(refusal)
    const loaded = (overrides: Partial<PracticeRosterMember>) => ({
      data: roster([member(), clinician(overrides)]),
      response: new Response(null, { status: 200 }),
    })
    practiceRoster
      .mockResolvedValueOnce(loaded({ access_ends_at: ENDS_AT }))
      .mockResolvedValueOnce(loaded({}))
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician()])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke Alex Active" }))
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Revoke access",
      }),
    )
    fireEvent.click(
      await screen.findByRole("button", { name: "Restore Alex Active" }),
    )

    expect(
      await screen.findByRole("button", { name: "Revoke Alex Active" }),
    ).toBeInTheDocument()
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("keeps a refused revoke's reason in view while the roster reloads", async () => {
    client.revokePracticeMember.mockResolvedValue({
      error: { detail: "This clinician's access has already ended." },
    })
    practiceRoster.mockResolvedValue({
      data: roster([member(), clinician()]),
      response: new Response(null, { status: 200 }),
    })
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician()])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke Alex Active" }))
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Revoke access" }),
    )

    await waitFor(() => expect(practiceRoster).toHaveBeenCalled())
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "This clinician's access has already ended.",
    )
  })

  it("moves a refused revoke's reason to the row when the reload replaces its button", async () => {
    client.revokePracticeMember.mockResolvedValue({
      error: { detail: "This clinician's access has already ended." },
    })
    practiceRoster.mockResolvedValue({
      data: roster([member(), clinician({ access_ends_at: ENDS_AT })]),
      response: new Response(null, { status: 200 }),
    })
    render(
      <PracticeRoster
        practice={practice()}
        roster={roster([member(), clinician()])}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Revoke Alex Active" }))
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Revoke access",
      }),
    )

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Restore Alex Active" }),
      ).toBeInTheDocument(),
    )
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    const row = screen.getAllByRole("listitem")[1]
    expect(within(row).getByRole("alert")).toHaveTextContent(
      "This clinician's access has already ended.",
    )
  })
})

describe("a cut-off passing while the roster is open", () => {
  const ENDS_AT = "2026-09-28T04:00:00Z"
  const ending = () =>
    roster([
      member(),
      member({
        member_id: "practice_member_alex",
        display_name: "Alex Active",
        role: "workforce",
        is_self: false,
        access_ends_at: ENDS_AT,
      }),
    ])
  const ended = () =>
    roster(
      [
        member(),
        member({
          member_id: "practice_member_alex",
          display_name: "Alex Active",
          role: "workforce",
          is_self: false,
          access_ends_at: ENDS_AT,
          seat_active: false,
          access_in_force: false,
        }),
      ],
      { occupied_seats: 1 },
    )

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it("reloads once the earliest cut-off has passed", async () => {
    vi.setSystemTime(new Date("2026-09-28T03:59:00Z"))
    practiceRoster.mockResolvedValue({
      data: ended(),
      response: new Response(null, { status: 200 }),
    })
    render(<PracticeRoster practice={practice()} roster={ending()} />)

    await act(() => vi.advanceTimersByTimeAsync(59_000))
    expect(practiceRoster).not.toHaveBeenCalled()
    await act(() => vi.advanceTimersByTimeAsync(2_000))

    expect(practiceRoster).toHaveBeenCalledTimes(1)
    expect(screen.getAllByRole("listitem")[1]).toHaveTextContent(
      /Access revoked · joined/,
    )
    expect(screen.getByText("1 seat in use")).toBeInTheDocument()
  })

  it("does not hammer the server when this clock runs ahead of it", async () => {
    vi.setSystemTime(new Date("2026-09-28T04:05:00Z"))
    practiceRoster.mockResolvedValue({
      data: ending(),
      response: new Response(null, { status: 200 }),
    })
    render(<PracticeRoster practice={practice()} roster={ending()} />)

    await act(() => vi.advanceTimersByTimeAsync(29_000))
    expect(practiceRoster).not.toHaveBeenCalled()
    await act(() => vi.advanceTimersByTimeAsync(2_000))
    expect(practiceRoster).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(29_000))
    expect(practiceRoster).toHaveBeenCalledTimes(1)
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
    await waitFor(() =>
      expect(client.createPracticeInvitation).toHaveBeenCalledTimes(2),
    )

    const keys = client.createPracticeInvitation.mock.calls.map(
      ([options]) => options.headers["Idempotency-Key"],
    )
    expect(new Set(keys).size).toBe(2)
  })

  function keys() {
    return client.createPracticeInvitation.mock.calls.map(
      ([options]) => options.headers["Idempotency-Key"],
    )
  }

  function submitAgain(email: string) {
    fireEvent.change(screen.getByLabelText("Invite a clinician"), {
      target: { value: email },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }))
  }

  it("retries an ambiguous failure with the same key", async () => {
    client.createPracticeInvitation
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({
        error: { detail: "Internal Server Error" },
        response: new Response(null, { status: 502 }),
      })
      .mockResolvedValue({ data: issued() })

    invite("retry@gmail.com")
    await screen.findByText("The invitation could not be sent.")
    submitAgain(" Retry@Gmail.com")
    await screen.findByText("Internal Server Error")
    submitAgain("retry@gmail.com")
    await screen.findByText("Send this link to new@gmail.com.")

    const [lost, failed, succeeded] = keys()
    expect(failed).toBe(lost)
    expect(succeeded).toBe(lost)
  })

  it("uses a new key after a success, a refusal, or a changed address", async () => {
    client.createPracticeInvitation
      .mockResolvedValueOnce({ data: issued() })
      .mockResolvedValueOnce({
        error: { detail: "An invitation to this address is already pending." },
        response: new Response(null, { status: 409 }),
      })
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue({ data: issued() })

    invite("same@gmail.com")
    await screen.findByText("Send this link to new@gmail.com.")
    submitAgain("same@gmail.com")
    await screen.findByText(/already pending/)
    submitAgain("same@gmail.com")
    await screen.findByText("The invitation could not be sent.")
    submitAgain("other@gmail.com")
    await waitFor(() =>
      expect(client.createPracticeInvitation).toHaveBeenCalledTimes(4),
    )

    expect(new Set(keys()).size).toBe(4)
  })

  it("says it is sending while the request is in flight", async () => {
    let finish: (value: unknown) => void = () => {}
    client.createPracticeInvitation.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )

    invite("slow@gmail.com")

    expect(
      await screen.findByRole("button", { name: "Sending…" }),
    ).toBeDisabled()
    finish({ data: issued() })
    expect(
      await screen.findByRole("button", { name: "Send invitation" }),
    ).toBeInTheDocument()
  })

  it("announces the link through a live region that was already present", async () => {
    client.createPracticeInvitation.mockResolvedValue({ data: issued() })
    render(<PracticeRoster practice={practice()} roster={roster([member()])} />)
    const status = screen.getByRole("status")
    expect(status).toBeEmptyDOMElement()

    submitAgain("new@gmail.com")

    await waitFor(() =>
      expect(status).toHaveTextContent("Send this link to new@gmail.com."),
    )
    expect(screen.getByRole("status")).toBe(status)
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
