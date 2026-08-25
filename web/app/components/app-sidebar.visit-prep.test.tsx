import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import { VisitPrepAccessProvider } from "~/configuration/visit-prep-access"

import { AppSidebar } from "./app-sidebar"
import { SidebarProvider } from "./ui/sidebar"
import { render, screen } from "@testing-library/react"

vi.mock("~/components/ViewAsAccountControl", () => ({
  ViewAsAccountControl: () => null,
}))

const unavailable = {
  eligible: false,
  can_manage: false,
  can_send_email: false,
  can_send_sms: false,
}

function SidebarWithAccess({ eligible }: { eligible: boolean }) {
  return (
    <VisitPrepAccessProvider access={{ ...unavailable, eligible }}>
      <MemoryRouter>
        <SidebarProvider>
          <AppSidebar isLoading={false} entries={false} />
        </SidebarProvider>
      </MemoryRouter>
    </VisitPrepAccessProvider>
  )
}

describe("AppSidebar Visit Prep navigation", () => {
  it("shows the navigation item only for an eligible physician", () => {
    const { rerender } = render(<SidebarWithAccess eligible={false} />)
    expect(screen.queryByText("Visit Prep")).not.toBeInTheDocument()

    rerender(<SidebarWithAccess eligible />)
    expect(screen.getByText("Visit Prep")).toBeInTheDocument()
  })
})
