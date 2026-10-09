import { MemoryRouter } from "react-router"
import { describe, expect, it } from "vitest"
import { vi } from "vitest"

import type { PracticeStatus } from "~/lib/practice-api"

import {
  AccountRecoveryRequired,
  BaaClickwrap,
  ManagedOrganizationAccessNotice,
  default as OnboardingPage,
  OrganizationAccessFlow,
  PracticeForm,
  canLeaveOnboarding,
} from "./onboarding"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const apiMocks = vi.hoisted(() => ({
  acceptPracticeBaa: vi.fn(),
  createPractice: vi.fn(),
  getBaaCurrent: vi.fn(),
}))

vi.mock("~/lib/practice-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/lib/practice-api")>()
  return {
    ...actual,
    acceptPracticeBaa: apiMocks.acceptPracticeBaa,
    createPractice: apiMocks.createPractice,
    getBaaCurrent: apiMocks.getBaaCurrent,
  }
})

vi.mock("@clerk/clerk-react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@clerk/clerk-react")>()),
  UserButton: () => null,
  useUser: () => ({ user: { primaryEmailAddress: { emailAddress: "provider@synthetic.invalid" } } }),
}))

function practice(overrides: Partial<PracticeStatus> = {}): PracticeStatus {
  return {
    legal_name: "Test Practice, PLLC",
    join_code: "TESTCODE",
    email_domain: "example.com",
    origin_channel: "direct",
    pastoral_pack: false,
    baa_accepted: false,
    role: "owner",
    attested: false,
    access_authorized: false,
    authorization_basis: null,
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
    entitled: false,
    subscription_status: null,
    seat_summary: null,
    ...overrides,
  }
}

describe("onboarding view-as safety", () => {
  it("renders only the verification interstitial when status is unavailable", () => {
    const props = {
      loaderData: {
        viewAsCheck: { state: "unavailable", status: null },
      },
    } as Parameters<typeof OnboardingPage>[0]

    render(<OnboardingPage {...props} />)

    expect(
      screen.getByText("Support-view status unavailable"),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("How will this Cosmas account be used?"),
    ).not.toBeInTheDocument()
  })
})

function openOnboarding(current: PracticeStatus) {
  const props = {
    loaderData: {
      accountRecoveryRequired: false,
      practice: current,
      organizationAccess: null,
      channel: "direct",
      email: "clinician@example.com",
      onboarding_completed: true,
      biography: "",
      viewAsCheck: {
        state: "ready",
        status: {
          request_binding: "inactive",
          can_view_as: false,
          active: false,
          read_only: false,
          diagnostic_code: null,
          expires_at: null,
          actor: null,
          target: null,
        },
      },
    },
  } as Parameters<typeof OnboardingPage>[0]
  render(
    <MemoryRouter>
      <OnboardingPage {...props} />
    </MemoryRouter>,
  )
}

describe("onboarding for a clinician the owner revoked", () => {
  const revoked = {
    role: "workforce",
    baa_accepted: true,
    attested: true,
    access_authorized: true,
    authorization_basis: "workforce_attestation",
    // A cut-off still ahead on this machine's clock.
    access_ends_at: "2099-09-28T04:00:00Z",
  }

  it("explains the ended access once the server says it has ended", () => {
    openOnboarding(practice({ ...revoked, access_ended: true }))

    expect(screen.getByRole("status")).toHaveTextContent(
      "Your practice owner ended your access to Cosmas." +
        "Ask your practice owner if you need it restored.",
    )
    expect(screen.queryByText("Optional setup")).not.toBeInTheDocument()
  })

  it("trusts the server, not the browser clock, before the cut-off", () => {
    // A cut-off already past on this machine's clock, but not the server's.
    openOnboarding(
      practice({
        ...revoked,
        access_ends_at: "2000-01-01T05:00:00Z",
        access_ended: false,
      }),
    )

    expect(screen.getByText("Optional setup")).toBeInTheDocument()
  })
})

describe("OrganizationAccessFlow", () => {
  it("masks the organization code from the browser and replay tools", () => {
    render(
      <OrganizationAccessFlow
        opportunity={{
          state: "claim",
          organization_name: "Test Organization",
          email_domain: "example.com",
        }}
        onboardingCompleted
      />,
    )

    const input = screen.getByLabelText("Organization access code")
    expect(input).toHaveAttribute("type", "password")
    expect(input).toHaveAttribute("data-sentry-mask")
    expect(input).toHaveAttribute("data-ph-no-capture")
  })

  it("keeps unprovisioned managed users out of self-service and BAA flows", () => {
    render(
      <ManagedOrganizationAccessNotice
        disposition={{
          state: "pending",
          organization_name: null,
          email_domain: "mycatholicdoctor.com",
        }}
      />,
    )

    expect(
      screen.getByRole("heading", {
        name: "Your organization is setting up access",
      }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/does not grant patient access/i),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /contact support/i }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining("Organization%20access%20review"),
    )
    expect(
      screen.queryByRole("button", { name: /Continue to the BAA/i }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText(/start your own practice/i),
    ).not.toBeInTheDocument()
  })

  it("uses one sanitized support state for unsafe managed assignments", () => {
    render(
      <ManagedOrganizationAccessNotice
        disposition={{
          state: "support",
          organization_name: null,
          email_domain: "fiathealth.org",
        }}
      />,
    )

    expect(
      screen.getByRole("heading", {
        name: "We need to review your organization access",
      }),
    ).toBeInTheDocument()
    expect(screen.getByText(/records remain safe/i)).toBeInTheDocument()
    expect(
      screen.getByText(/no new agreement is required/i),
    ).toBeInTheDocument()
  })

  it("uses the server access decision instead of requiring a practice payload", () => {
    expect(
      canLeaveOnboarding({
        onboarding_completed: true,
        is_admin: false,
        practice_ready: true,
      }),
    ).toBe(true)
    expect(
      canLeaveOnboarding({
        onboarding_completed: true,
        is_admin: true,
        practice_ready: false,
      }),
    ).toBe(false)
  })
})

describe("PracticeForm onboarding guardrails", () => {
  it("shows and confirms the controlling domain only for a group", () => {
    render(
      <PracticeForm
        onPractice={vi.fn()}
        onBack={vi.fn()}
        channel="direct"
        mismatch={false}
        email="doctor@clinic.example"
      />,
    )

    expect(screen.getByText("@clinic.example")).toBeInTheDocument()
    expect(
      screen.queryByRole("checkbox", { name: /I confirm that/i }),
    ).not.toBeInTheDocument()

    fireEvent.click(
      screen.getByRole("radio", { name: /Group — I will invite workforce/i }),
    )

    const confirmation = screen.getByRole("checkbox", {
      name: /I confirm that @clinic.example/i,
    })
    expect(confirmation).not.toBeChecked()
    expect(
      screen.getByRole("button", { name: /Continue to the BAA/i }),
    ).toBeDisabled()

    fireEvent.click(confirmation)
    expect(
      screen.getByRole("button", { name: /Continue to the BAA/i }),
    ).not.toBeDisabled()
  })

  it("keeps consumer-email practices solo and markets the pastoral pack as coming soon", () => {
    render(
      <PracticeForm
        onPractice={vi.fn()}
        onBack={vi.fn()}
        channel="direct"
        mismatch={false}
        email="doctor@gmail.com"
      />,
    )

    expect(
      screen.getByRole("radio", { name: /Group — I will invite workforce/i }),
    ).toBeDisabled()
    expect(screen.getByText(/Coming soon:/i)).toBeInTheDocument()
    expect(
      screen.getByRole("checkbox", { name: /Coming soon:/i }),
    ).toBeDisabled()
  })

  it("never renders or submits NPI during public practice creation", async () => {
    apiMocks.createPractice.mockReset().mockResolvedValueOnce({})
    const onPractice = vi.fn()
    render(
      <PracticeForm
        onPractice={onPractice}
        onBack={vi.fn()}
        channel="direct"
        mismatch={false}
        email="doctor@clinic.example"
      />,
    )

    expect(screen.queryByLabelText(/NPI/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/NPI/i)).not.toBeInTheDocument()

    fireEvent.change(
      screen.getByLabelText("Legal name of the covered entity"),
      { target: { value: "NPI-Free Practice, PLLC" } },
    )
    fireEvent.click(
      screen.getByRole("button", { name: /Continue to the BAA/i }),
    )

    await waitFor(() => expect(onPractice).toHaveBeenCalledOnce())
    expect(apiMocks.createPractice).toHaveBeenCalledWith({
      legal_name: "NPI-Free Practice, PLLC",
      city: "",
      region: "",
      is_solo: true,
      channel: "direct",
      pastoral_pack: false,
    })
  })
})

describe("BaaClickwrap", () => {
  it("accepts the exact document identity fetched and displayed", async () => {
    const current = {
      doc_id: "CN-BAA-CUST-CW",
      version: "2.3",
      sha256: "f".repeat(64),
      html: "<p>Current agreement body</p>",
    }
    const accepted = practice({ baa_accepted: true })
    apiMocks.getBaaCurrent.mockReset().mockResolvedValueOnce(current)
    apiMocks.acceptPracticeBaa.mockReset().mockResolvedValueOnce(accepted)
    const onPractice = vi.fn()

    render(
      <BaaClickwrap
        practice={practice()}
        onPractice={onPractice}
        onDecline={vi.fn()}
      />,
    )

    await screen.findByText("Current agreement body")
    fireEvent.change(
      screen.getByRole("textbox", { name: /accepting individual/i }),
      { target: { value: "Pat Owner" } },
    )
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), {
      target: { value: "Physician / owner" },
    })
    fireEvent.click(
      screen.getByRole("checkbox", { name: /I have read and agree/i }),
    )
    fireEvent.click(screen.getByRole("button", { name: "I Agree" }))

    await waitFor(() => {
      expect(apiMocks.acceptPracticeBaa).toHaveBeenCalledWith({
        doc_id: current.doc_id,
        version: current.version,
        sha256: current.sha256,
        signature_name: "Pat Owner",
        signature_title: "Physician / owner",
        checks: ["baa"],
      })
      expect(onPractice).toHaveBeenCalledWith(accepted)
    })
  })

  it("fails closed when the backend document identity does not match the bundled agreement", async () => {
    apiMocks.getBaaCurrent.mockReset().mockResolvedValueOnce({
      doc_id: "CN-BAA-CUST-CW",
      version: "2.2",
      sha256: "a".repeat(64),
      html: "<p>Stale agreement body</p>",
    })
    apiMocks.acceptPracticeBaa.mockReset()

    render(
      <BaaClickwrap
        practice={practice()}
        onPractice={vi.fn()}
        onDecline={vi.fn()}
      />,
    )

    expect(
      await screen.findByText(/agreement version could not be verified/i),
    ).toBeInTheDocument()
    expect(screen.queryByText("Stale agreement body")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "I Agree" })).toBeDisabled()
    expect(apiMocks.acceptPracticeBaa).not.toHaveBeenCalled()
  })
})

describe("AccountRecoveryRequired", () => {
  it("gives a safe, actionable recovery message", () => {
    render(<AccountRecoveryRequired />)

    expect(
      screen.getByRole("heading", { name: "Your records are safe" }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/linked to a previous sign-in/i),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /contact support/i }),
    ).toHaveAttribute("href", "mailto:info@cosmaneura.com")
  })
})
