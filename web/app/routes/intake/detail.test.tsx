import { describe, expect, it, vi } from "vitest"

import IntakeDetailPage, { EmptyState } from "./detail"
import { render, screen } from "@testing-library/react"

vi.mock("~/configuration/client", () => ({ intakeDetail: vi.fn() }))
vi.mock("~/components/shared/PageHeader", () => ({ PageHeader: () => null }))
vi.mock("~/components/shared/InnerScrollableContent", () => ({
  InnerScrollableContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}))
vi.mock("@/components/ui/sidebar", () => ({
  SidebarInset: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}))
vi.mock("~/utils/terminology", () => ({
  useTerminology: () => ({ patient: "client" }),
}))

const PUBLIC_URL = "https://intake.example.test/chat/synthetic-token"

describe("intake waiting state", () => {
  it("explains the wait and offers the intake link to copy", () => {
    render(<EmptyState intake={{ public_url: PUBLIC_URL }} />)

    expect(
      screen.getByRole("heading", { name: "Waiting on your client" }),
    ).toBeInTheDocument()
    const link = screen.getByRole("link", { name: PUBLIC_URL })
    expect(link).toHaveAttribute("href", PUBLIC_URL)
    expect(link).toHaveAttribute("data-ph-no-capture")
    expect(link).toHaveAttribute("data-sentry-block")
    expect(
      screen.getByRole("button", { name: "Copy intake link" }),
    ).toBeInTheDocument()
  })

  it("omits the link controls when no link exists", () => {
    render(<EmptyState intake={{ public_url: null }} />)

    expect(screen.queryByRole("link")).toBeNull()
    expect(
      screen.queryByRole("button", { name: "Copy intake link" }),
    ).toBeNull()
  })
})

describe("intake detail status", () => {
  function showIntake(
    intake_status: "pending" | "processing" | "complete" | "none",
    summary: string | null,
  ) {
    const loaderData = {
      appointment: {
        id: "appointment_test",
        appointment_date: "2026-10-08T15:00:00Z",
        intake_status,
      },
      patient: { id: "patient_test", name: "Synthetic Person" },
      intake: { summary, public_url: null },
    }
    render(
      <IntakeDetailPage
        {...({ loaderData } as unknown as Parameters<
          typeof IntakeDetailPage
        >[0])}
      />,
    )
  }

  it.each([
    ["pending", null, "Awaiting client"],
    ["processing", null, "Processing"],
    ["complete", "## Summary", "Completed"],
    ["pending", "## Summary", "Completed"],
  ] as const)("shows %s with summary %s as %s", (status, summary, label) => {
    showIntake(status, summary)
    expect(screen.getByText(label)).toBeInTheDocument()
  })
})
