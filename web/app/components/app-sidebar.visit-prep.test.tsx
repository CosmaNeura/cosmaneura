import { MemoryRouter, useLocation } from "react-router"
import { describe, expect, it, vi } from "vitest"

import { SidebarProvider } from "~/components/ui/sidebar"
import { VisitPrepAccessProvider } from "~/configuration/visit-prep-access"

import { AppSidebar } from "./app-sidebar"
import { fireEvent, render, screen } from "@testing-library/react"

vi.mock("~/components/ViewAsAccountControl", () => ({
  ViewAsAccountControl: () => null,
}))
vi.mock("~/components/brand/BrandLogo", () => ({
  BrandLogo: () => <span>Cosmas</span>,
}))

const allowed = {
  eligible: true,
  can_manage: true,
  can_send_email: false,
  can_send_sms: false,
}

function Location() {
  return <output aria-label="Current route">{useLocation().pathname}</output>
}

describe("Visit Prep dashboard navigation", () => {
  it("hides the entry by default, exposes server-eligible access, and removes it when withdrawn", () => {
    const page = (access: typeof allowed | null) => (
      <MemoryRouter initialEntries={["/notes"]}>
        <VisitPrepAccessProvider access={access}>
          <SidebarProvider>
            <AppSidebar isLoading={false} entries={false} />
            <Location />
          </SidebarProvider>
        </VisitPrepAccessProvider>
      </MemoryRouter>
    )
    const { rerender } = render(page(null))
    expect(
      screen.queryByRole("button", { name: "Visit Prep" }),
    ).not.toBeInTheDocument()
    rerender(page(allowed))
    fireEvent.click(screen.getByRole("button", { name: "Visit Prep" }))
    expect(screen.getByLabelText("Current route")).toHaveTextContent(
      "/visit-preps",
    )
    rerender(page({ ...allowed, eligible: false, can_manage: false }))
    expect(
      screen.queryByRole("button", { name: "Visit Prep" }),
    ).not.toBeInTheDocument()
  })
})
