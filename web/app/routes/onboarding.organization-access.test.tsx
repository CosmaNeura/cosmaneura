import { describe, expect, it } from "vitest"

import { OrganizationAccessFlow } from "./onboarding"
import { render, screen } from "@testing-library/react"

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
})
