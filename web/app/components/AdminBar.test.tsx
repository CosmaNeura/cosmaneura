import { afterEach, describe, expect, it, vi } from "vitest"

import AdminBar from "./AdminBar"
import { render, screen } from "@testing-library/react"

vi.mock("~/lib/view-as-api", () => ({
  beginViewAs: vi.fn(),
  endViewAs: vi.fn(),
  getViewAsStatus: vi.fn(),
  getViewAsTargets: vi.fn(),
  viewAsDiagnosticCopy: vi.fn(() => "Account not ready."),
}))

describe("AdminBar", () => {
  afterEach(() => vi.clearAllMocks())

  it("keeps actor, target, read-only state, and Exit visible", () => {
    render(
      <AdminBar
        initialStatus={{
          request_binding:
            "vab1.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          can_view_as: true,
          active: true,
          read_only: true,
          diagnostic_code: null,
          expires_at: "2099-08-24T17:15:00Z",
          actor: {
            doctor_id: "doctor_actor",
            email: "actor@example.com",
          },
          target: {
            doctor_id: "doctor_target",
            email: "target@example.com",
          },
        }}
      />,
    )

    expect(screen.getByText("Read-only view-as")).toBeInTheDocument()
    expect(
      screen.getByText("actor@example.com → target@example.com"),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Exit view-as" }),
    ).toBeInTheDocument()
  })

  it("renders no support control without an exact capability", () => {
    const { container } = render(
      <AdminBar
        initialStatus={{
          request_binding: "inactive",
          can_view_as: false,
          active: false,
          read_only: false,
          diagnostic_code: null,
          expires_at: null,
          actor: null,
          target: null,
        }}
      />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})
