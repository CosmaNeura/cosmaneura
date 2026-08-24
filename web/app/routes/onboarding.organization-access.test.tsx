import { describe, expect, it } from "vitest"
import { vi } from "vitest"

import {
  AccountRecoveryRequired,
  OrganizationAccessFlow,
  PracticeForm,
  canLeaveOnboarding,
} from "./onboarding"
import { fireEvent, render, screen } from "@testing-library/react"

describe("OrganizationAccessFlow", () => {
  it("masks the organization code from the browser and replay tools", () => {
    render(
      <OrganizationAccessFlow
        opportunity={{
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
        is_admin: false,
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

  it("shows a clear NPI error instead of truncating input", () => {
    render(
      <PracticeForm
        onPractice={vi.fn()}
        onBack={vi.fn()}
        channel="direct"
        mismatch={null}
        email="doctor@clinic.example"
      />,
    )

    const input = screen.getByLabelText("Organizational NPI (optional)")
    fireEvent.change(input, { target: { value: "12345678901" } })

    expect(input).toHaveValue("12345678901")
    expect(screen.getByRole("alert")).toHaveTextContent(
      "NPI must be exactly 10 digits.",
    )
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
