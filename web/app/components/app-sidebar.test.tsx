import { MemoryRouter, useLocation } from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "~/components/ui/sidebar"

import { AppSidebar, type SidebarEntry } from "./app-sidebar"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

vi.mock("~/components/ViewAsAccountControl", () => ({
  ViewAsAccountControl: () => null,
}))

vi.mock("~/components/brand/BrandLogo", () => ({
  BrandLogo: () => <span>Cosmas</span>,
}))

const meeting: SidebarEntry = {
  id: "meeting-1",
  url: "/schedule/meeting-1",
  title: "Synthetic meeting",
  location: "Zoom",
  date: "Aug 28",
  status: "none",
  preview: "10:00 AM",
}

function CurrentLocation() {
  return <output aria-label="Current location">{useLocation().pathname}</output>
}

function MobileDrawerState() {
  const { isMobile, openMobile } = useSidebar()
  return (
    <output aria-label="Mobile drawer state" data-mobile={String(isMobile)}>
      {String(openMobile)}
    </output>
  )
}

describe("AppSidebar on mobile", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 390,
    })
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("opens the meeting list and closes it after selecting a meeting", async () => {
    render(
      <MemoryRouter initialEntries={["/schedule"]}>
        <SidebarProvider>
          <SidebarTrigger />
          <AppSidebar isLoading={false} entries={[meeting]} header="Schedule" />
          <CurrentLocation />
          <MobileDrawerState />
        </SidebarProvider>
      </MemoryRouter>,
    )

    await waitFor(() =>
      expect(screen.getByLabelText("Mobile drawer state")).toHaveAttribute(
        "data-mobile",
        "true",
      ),
    )
    fireEvent.click(screen.getByRole("button", { name: "Toggle Sidebar" }))

    expect(document.querySelector('[data-mobile="true"]')).toBeInTheDocument()
    const meetingButton = await screen.findByRole("button", {
      name: /synthetic meeting/i,
    })
    expect(screen.getByLabelText("Mobile drawer state")).toHaveTextContent(
      "true",
    )

    fireEvent.click(meetingButton)

    await waitFor(() =>
      expect(screen.getByLabelText("Current location")).toHaveTextContent(
        "/schedule/meeting-1",
      ),
    )
    expect(screen.getByLabelText("Mobile drawer state")).toHaveTextContent(
      "false",
    )
  })
})
