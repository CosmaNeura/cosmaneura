import { describe, expect, it } from "vitest"
import { vi } from "vitest"

import {
  AccountRecoveryRequired,
  ManagedOrganizationAccessNotice,
  default as OnboardingPage,
  OrganizationAccessFlow,
  PracticeForm,
  canLeaveOnboarding,
} from "./onboarding"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const apiMocks = vi.hoisted(() => ({
  createPractice: vi.fn(),
}))

vi.mock("~/lib/practice-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/lib/practice-api")>()
  return { ...actual, createPractice: apiMocks.createPractice }
})

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
        mismatch={null}
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
        mismatch={null}
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
        mismatch={null}
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
