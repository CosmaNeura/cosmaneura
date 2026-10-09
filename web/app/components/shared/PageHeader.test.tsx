import type { PropsWithChildren } from "react"
import { describe, expect, it, vi } from "vitest"

import { PageHeader } from "./PageHeader"
import { render, screen } from "@testing-library/react"

vi.mock("@/components/ui/sidebar", () => ({
  SidebarTrigger: () => null,
}))
vi.mock("@/components/ui/separator", () => ({
  Separator: ({ children }: PropsWithChildren) => <>{children}</>,
}))

describe("PageHeader", () => {
  it("renders a section title as the page heading", () => {
    render(<PageHeader title="Notes" isPageTitle />)

    expect(
      screen.getByRole("heading", { level: 1, name: "Notes" }),
    ).toBeInTheDocument()
  })

  it("keeps a record title out of the heading outline and analytics", () => {
    render(
      <PageHeader
        title="Synthetic Patient"
        actions={<button>Upload</button>}
      />,
    )

    expect(screen.queryByRole("heading")).toBeNull()
    const title = screen.getByText("Synthetic Patient")
    expect(title).toHaveAttribute("data-ph-no-capture")
    expect(title).toHaveAttribute("data-sentry-block")
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument()
  })
})
