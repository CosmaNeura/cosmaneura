import { beforeEach, describe, expect, it, vi } from "vitest"

import type {
  CapabilityDecision,
  PublicCapabilityEntitlement,
  PublicOnboardingState,
} from "~/lib/practice-api"

import {
  CapabilityStatus,
  PublicCheckoutStep,
  PublicCredentialStatus,
  PublicInvitationAcceptance,
  PublicInvitationAccountMismatch,
  PublicPracticeTeam,
  PublicPracticeTeamLists,
  consumePublicInvitationLink,
  finishPublicInvitationFlow,
  publicInvitationUrl,
  publicOnboardingCanUsePhi,
  shouldLeaveOnboarding,
} from "./onboarding"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const apiMocks = vi.hoisted(() => ({
  createCheckout: vi.fn(),
  createInvitation: vi.fn(),
  getInvitations: vi.fn(async () => []),
  getMembers: vi.fn(async () => []),
}))

vi.mock("~/lib/practice-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/lib/practice-api")>()
  return {
    ...actual,
    createBillingCheckout: apiMocks.createCheckout,
    createPublicPracticeInvitation: apiMocks.createInvitation,
    getPublicPracticeInvitations: apiMocks.getInvitations,
    getPublicPracticeMembers: apiMocks.getMembers,
  }
})

const VALID_INVITATION_TOKEN = "a".repeat(43)
const INVITATION_IDEMPOTENCY_KEY = "11111111-1111-4111-8111-111111111111"

function installSerializedCrossTabLocks() {
  let tail = Promise.resolve<unknown>(undefined)
  const request = vi.fn((...args: unknown[]) => {
    const callback = args.at(-1) as (lock: Lock) => unknown | Promise<unknown>
    const result = tail.then(() =>
      callback({ name: String(args[0]), mode: "exclusive" } as Lock),
    )
    tail = result.then(
      () => undefined,
      () => undefined,
    )
    return result
  })
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: { request } as unknown as LockManager,
  })
}

const allowedRealPhi: CapabilityDecision = {
  decision: "allowed",
  data_mode: "real_phi",
  rollout: "enabled",
  operation_mode: "standard",
  reason_codes: ["allowed"],
}

function capabilityContract(): PublicCapabilityEntitlement {
  return {
    contract_version: "public-capability-entitlements.v1",
    policy_revision: "2026-08-27",
    generated_at: "2026-08-27T12:00:00Z",
    actor: {
      actor_id: "doctor_test",
      practice_id: "practice_test",
      role: "owner",
      membership_state: "active",
    },
    gates: {
      email_verified: true,
      baa_accepted: true,
      terms_accepted: true,
      privacy_accepted: true,
      subscription_state: "trialing",
      credential_required: true,
      credential_state: "pending",
    },
    seats: {
      active_clinician_seats: 2,
      maximum_active_clinician_seats: 20,
      pending_invitations: 1,
      stripe_quantity_sync_required: false,
    },
    capabilities: {
      soap_note: allowedRealPhi,
      intake_prep: allowedRealPhi,
      session_prep: { ...allowedRealPhi, operation_mode: "read_only" },
      scribe: { ...allowedRealPhi, operation_mode: "web_pwa_first" },
      cab: { ...allowedRealPhi, operation_mode: "advisory_only" },
    },
  }
}

function pendingCredentialState(): PublicOnboardingState {
  return {
    contract_version: "public-onboarding.v1",
    managed_path: false,
    completed_steps: ["account", "practice", "baa", "legal", "checkout"],
    current_step: "credential",
    practice: { legal_name: "Pilot Practice", role: "owner" },
    baa_accepted: true,
    terms_accepted: true,
    privacy_accepted: true,
    checkout_started: true,
    subscription_state: "trialing",
    trial_ends_at: "2026-09-10T12:00:00Z",
    credential_required: true,
    credential_state: "pending",
    phi_access_allowed: false,
  }
}

describe("public invitation bearer lifecycle", () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    window.history.replaceState({}, "", "/onboarding")
  })

  it("retains a valid token across reload until acceptance or explicit discard", () => {
    window.history.replaceState(
      {},
      "",
      `/onboarding#registrationToken=${VALID_INVITATION_TOKEN}`,
    )

    expect(consumePublicInvitationLink()).toEqual({
      token: VALID_INVITATION_TOKEN,
      invalid: false,
    })
    expect(window.location.hash).toBe("")
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(1)
    expect(consumePublicInvitationLink()).toEqual({
      token: VALID_INVITATION_TOKEN,
      invalid: false,
    })

    const navigate = vi.fn()
    finishPublicInvitationFlow(navigate)

    expect(window.sessionStorage.length).toBe(0)
    expect(window.location.href).not.toContain(VALID_INVITATION_TOKEN)
    expect(navigate).toHaveBeenCalledOnce()
  })

  it("rejects malformed fragment material without persisting it", () => {
    window.history.replaceState(
      {},
      "",
      "/onboarding#registrationToken=too-short",
    )

    expect(consumePublicInvitationLink()).toEqual({
      token: null,
      invalid: true,
    })
    expect(window.location.hash).toBe("")
    expect(window.sessionStorage.length).toBe(0)
    expect(window.localStorage.length).toBe(0)
  })

  it("builds share links with no bearer in the request URL", () => {
    const link = new URL(publicInvitationUrl(VALID_INVITATION_TOKEN))

    expect(link.search).toBe("")
    expect(link.hash).toBe(`#registrationToken=${VALID_INVITATION_TOKEN}`)
    expect(link.pathname).toBe("/onboarding")
  })

  it("suppresses the normal completion redirect while a valid invite is pending", () => {
    const completed = {
      onboarding_completed: true,
      is_admin: false,
      practice_ready: true,
    }

    expect(
      shouldLeaveOnboarding(completed, {
        token: VALID_INVITATION_TOKEN,
        invalid: false,
      }),
    ).toBe(false)
    expect(
      shouldLeaveOnboarding(completed, { token: null, invalid: false }),
    ).toBe(true)
  })
})

describe("public invitation acceptance", () => {
  it("masks the bearer and requires all workforce acknowledgements", () => {
    render(<PublicInvitationAcceptance initialToken={VALID_INVITATION_TOKEN} />)

    const token = screen.getByLabelText("Invitation token")
    expect(token).toHaveAttribute("type", "password")
    expect(token).toHaveAttribute("data-sentry-mask")
    expect(token).toHaveAttribute("data-ph-no-capture")

    const submit = screen.getByRole("button", {
      name: "Attest and join practice",
    })
    expect(submit).toBeDisabled()

    fireEvent.change(screen.getByLabelText("Typed name"), {
      target: { value: "Test Clinician" },
    })
    for (const checkbox of screen.getAllByRole("checkbox")) {
      fireEvent.click(checkbox)
    }

    expect(screen.getAllByRole("checkbox")).toHaveLength(4)
    expect(submit).toBeEnabled()
  })

  it("surfaces an existing-practice collision with an explicit discard action", () => {
    const onAbandon = vi.fn()

    render(
      <PublicInvitationAccountMismatch
        practiceName="Existing Practice"
        invalidInvitation={false}
        onAbandon={onAbandon}
      />,
    )

    expect(
      screen.getByText(/already belongs to Existing Practice/i),
    ).toBeVisible()
    fireEvent.click(
      screen.getByRole("button", {
        name: "Discard invitation and continue",
      }),
    )
    expect(onAbandon).toHaveBeenCalledOnce()
  })
})

describe("public invitation creation recovery", () => {
  beforeEach(() => {
    window.localStorage.clear()
    apiMocks.createInvitation.mockReset()
    apiMocks.getInvitations.mockReset().mockResolvedValue([])
    apiMocks.getMembers.mockReset().mockResolvedValue([])
    installSerializedCrossTabLocks()
    vi.spyOn(crypto, "randomUUID").mockReturnValue(INVITATION_IDEMPOTENCY_KEY)
  })

  it("retries one persisted mutation after response loss and clears it only after acknowledgement", async () => {
    const invitation = {
      invitation_id: "invitation_test",
      invitee_email: "clinician@example.test",
      status: "pending" as const,
      role: "workforce" as const,
      issued_at: "2026-08-27T12:00:00Z",
      expires_at: "2026-09-03T12:00:00Z",
      invite_token: VALID_INVITATION_TOKEN,
      reused_idempotency_key: true,
    }
    apiMocks.createInvitation
      .mockImplementationOnce(async () => {
        expect(window.localStorage.length).toBe(1)
        throw new TypeError("response connection lost")
      })
      .mockResolvedValueOnce(invitation)

    const firstRender = render(
      <PublicPracticeTeam
        practiceName="Pilot Practice"
        ownerEmail="owner@example.test"
        capabilities={capabilityContract()}
        onEntitlementsRefresh={vi.fn(async () => {})}
      />,
    )
    fireEvent.change(screen.getByLabelText("Verified work email"), {
      target: { value: invitation.invitee_email },
    })
    fireEvent.click(screen.getByRole("button", { name: "Create invitation" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "response connection lost",
    )
    const firstKey = apiMocks.createInvitation.mock.calls[0]?.[1]
    expect(firstKey).toBe(INVITATION_IDEMPOTENCY_KEY)
    expect(window.localStorage.length).toBe(1)
    firstRender.unmount()

    render(
      <PublicPracticeTeam
        practiceName="Pilot Practice"
        ownerEmail="owner@example.test"
        capabilities={capabilityContract()}
        onEntitlementsRefresh={vi.fn(async () => {})}
      />,
    )
    await waitFor(() => {
      expect(screen.getByLabelText("Verified work email")).toHaveValue(
        invitation.invitee_email,
      )
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Retry saved invitation" }),
    )

    const generatedLink = await screen.findByLabelText(
      "Secure workforce invitation link",
    )
    expect(generatedLink).toHaveValue(
      `http://localhost:3000/onboarding#registrationToken=${VALID_INVITATION_TOKEN}`,
    )
    expect(apiMocks.createInvitation.mock.calls[1]?.[1]).toBe(firstKey)
    expect(window.localStorage.length).toBe(1)
    const journalKey = window.localStorage.key(0)
    expect(journalKey).not.toBeNull()
    expect(window.localStorage.getItem(journalKey!)).not.toContain(
      VALID_INVITATION_TOKEN,
    )

    fireEvent.click(screen.getByRole("button", { name: "I saved this link" }))
    await waitFor(() => expect(window.localStorage.length).toBe(0))
  })
})

describe("public capability status", () => {
  it("requires both approved credentials and the server PHI decision", () => {
    const pending = pendingCredentialState()

    expect(
      publicOnboardingCanUsePhi({ ...pending, phi_access_allowed: true }),
    ).toBe(false)
    expect(
      publicOnboardingCanUsePhi({
        ...pending,
        credential_state: "approved",
        phi_access_allowed: false,
      }),
    ).toBe(false)
    expect(
      publicOnboardingCanUsePhi({
        ...pending,
        credential_state: "approved",
        phi_access_allowed: true,
      }),
    ).toBe(true)
  })

  it("keeps contradictory real-PHI decisions locked while credentials are pending", () => {
    render(
      <PublicCredentialStatus
        state={pendingCredentialState()}
        capabilities={capabilityContract()}
        capabilitiesError={null}
        refreshError={null}
        onRefresh={vi.fn(async () => {})}
      />,
    )

    expect(screen.getByText("PHI access: locked")).toBeInTheDocument()
    expect(screen.getAllByText("Locked")).toHaveLength(5)
    expect(
      screen.queryByText("Available for patient care"),
    ).not.toBeInTheDocument()
  })

  it("states the five-capability pilot constraints without widening rollout", () => {
    render(
      <CapabilityStatus
        capabilities={capabilityContract()}
        error={null}
        phiAccessAllowed={false}
      />,
    )

    expect(screen.getByText("SOAP Note")).toBeInTheDocument()
    expect(screen.getByText("Intake Prep")).toBeInTheDocument()
    expect(screen.getByText("Session Prep (read-only)")).toBeInTheDocument()
    expect(screen.getByText("Scribe (web/PWA first)")).toBeInTheDocument()
    expect(screen.getByText("CAB (advisory only)")).toBeInTheDocument()
    expect(screen.getByText(/exactly five versioned questions/i)).toBeVisible()
    expect(screen.getByText(/wording and order/i)).toBeVisible()
    expect(screen.getByText(/native-mobile clients later/i)).toBeVisible()
    expect(screen.getByText(/no EHR write or claim submission/i)).toBeVisible()
  })
})

describe("public Checkout step", () => {
  beforeEach(() => {
    apiMocks.createCheckout.mockReset()
  })

  it("starts owner Checkout with the fixed-clock and credential-gate copy", () => {
    apiMocks.createCheckout.mockReturnValue(new Promise(() => {}))
    const state: PublicOnboardingState = {
      ...pendingCredentialState(),
      completed_steps: ["account", "practice", "baa", "legal"],
      current_step: "checkout",
      checkout_started: false,
      subscription_state: "none",
      trial_ends_at: null,
    }

    render(<PublicCheckoutStep state={state} />)

    expect(screen.getByText(/\$89 per active clinician/i)).toBeVisible()
    expect(
      screen.getByText(/card is collected in secure Checkout/i),
    ).toBeVisible()
    expect(screen.getByText(/fixed 14-day trial begins/i)).toBeVisible()
    expect(screen.getByText(/PHI.*remain locked/i)).toBeVisible()
    fireEvent.click(
      screen.getByRole("button", { name: "Add card and start 14-day trial" }),
    )
    expect(apiMocks.createCheckout).toHaveBeenCalledWith("/onboarding")
  })

  it("never claims an existing Checkout clock will restart", () => {
    apiMocks.createCheckout.mockReturnValue(new Promise(() => {}))
    const state: PublicOnboardingState = {
      ...pendingCredentialState(),
      current_step: "checkout",
      subscription_state: "incomplete_expired",
    }

    render(<PublicCheckoutStep state={state} />)

    expect(screen.getByText(/does not restart or extend it/i)).toBeVisible()
    expect(screen.getByText(/Checkout expired/i)).toBeVisible()
    expect(
      screen.queryByRole("button", { name: /start 14-day trial/i }),
    ).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", { name: "Continue secure Checkout" }),
    )
    expect(apiMocks.createCheckout).toHaveBeenCalledWith("/onboarding")
  })

  it("does not offer a workforce member the owner Checkout mutation", () => {
    render(
      <PublicCheckoutStep
        state={{
          ...pendingCredentialState(),
          current_step: "checkout",
          practice: { legal_name: "Pilot Practice", role: "workforce" },
        }}
      />,
    )

    expect(screen.queryByRole("button")).not.toBeInTheDocument()
    expect(screen.getByText(/practice owner must continue/i)).toBeVisible()
    expect(apiMocks.createCheckout).not.toHaveBeenCalled()
  })
})

describe("public owner team controls", () => {
  it("offers only supported invitation and workforce seat mutations", () => {
    const onRevokeInvitation = vi.fn(async () => {})
    const onSetMemberActive = vi.fn(async () => {})
    const pendingInvitation = {
      invitation_id: "invitation_pending",
      invitee_email: "pending-clinician@example.test",
      status: "pending" as const,
      role: "workforce" as const,
      issued_at: "2026-08-27T12:00:00Z",
      expires_at: "2026-09-03T12:00:00Z",
      invite_token: null,
      reused_idempotency_key: false,
    }
    const activeMember = {
      member_id: "member_active",
      email: "active-clinician@example.test",
      role: "workforce" as const,
      seat_state: "active" as const,
      credential_state: "pending" as const,
      access_revoked: false,
    }
    const inactiveMember = {
      member_id: "member_inactive",
      email: "inactive-clinician@example.test",
      role: "workforce" as const,
      seat_state: "inactive" as const,
      credential_state: "pending" as const,
      access_revoked: false,
    }

    render(
      <PublicPracticeTeamLists
        invitations={[
          pendingInvitation,
          {
            invitation_id: "invitation_accepted",
            invitee_email: "",
            status: "accepted",
            role: "workforce",
            issued_at: "2026-08-26T12:00:00Z",
            expires_at: "2026-09-02T12:00:00Z",
            invite_token: null,
            reused_idempotency_key: false,
          },
        ]}
        members={[
          {
            member_id: "member_owner",
            email: "owner@example.test",
            role: "owner",
            seat_state: "active",
            credential_state: "approved",
            access_revoked: false,
          },
          activeMember,
          inactiveMember,
          {
            member_id: "member_revoked",
            email: null,
            role: "workforce",
            seat_state: "inactive",
            credential_state: "rejected",
            access_revoked: true,
          },
        ]}
        loading={false}
        busyAction={null}
        onRevokeInvitation={onRevokeInvitation}
        onSetMemberActive={onSetMemberActive}
      />,
    )

    const invitationIdentity = screen.getByText(
      "pending-clinician@example.test",
    )
    expect(invitationIdentity).toHaveAttribute("data-sentry-mask")
    expect(invitationIdentity).toHaveAttribute("data-ph-no-capture")
    const memberIdentity = screen.getByText("active-clinician@example.test")
    expect(memberIdentity).toHaveAttribute("data-sentry-mask")
    expect(memberIdentity).toHaveAttribute("data-ph-no-capture")
    expect(screen.getAllByText("Email unavailable")).toHaveLength(2)

    fireEvent.click(
      screen.getByRole("button", {
        name: "Revoke invitation for pending-clinician@example.test",
      }),
    )
    fireEvent.click(
      screen.getByRole("button", {
        name: "Deactivate clinician seat for active-clinician@example.test",
      }),
    )
    fireEvent.click(
      screen.getByRole("button", {
        name: "Reactivate clinician seat for inactive-clinician@example.test",
      }),
    )

    expect(onRevokeInvitation).toHaveBeenCalledWith(pendingInvitation)
    expect(onSetMemberActive).toHaveBeenNthCalledWith(1, activeMember, false)
    expect(onSetMemberActive).toHaveBeenNthCalledWith(2, inactiveMember, true)
  })
})
