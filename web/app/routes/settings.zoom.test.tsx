import { RouterProvider, createMemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ZoomConnect } from "./settings"
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"

const client = vi.hoisted(() => ({
  settingsData: vi.fn(),
  practiceRoster: vi.fn(),
  updateSettingsData: vi.fn(),
  zoomAuthUrl: vi.fn(),
  zoomDisconnect: vi.fn(),
}))
vi.mock("~/configuration/client", () => client)

const toast = vi.hoisted(() => vi.fn())
vi.mock("~/hooks/use-toast", () => ({ toast }))
vi.mock("@clerk/clerk-react", () => ({ useUser: () => ({ user: null }) }))
vi.mock("~/utils/zoom-feature", () => ({ isZoomEnabled: () => true }))
vi.mock("~/utils/terminology", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/utils/terminology")>()
  return { ...actual, useTerminology: () => actual.getTerminology(null) }
})

function renderZoom() {
  const router = createMemoryRouter(
    [{ path: "/", loader: () => null, element: <ZoomConnect zoomConnected /> }],
    { initialEntries: ["/"] },
  )
  render(<RouterProvider router={router} />)
}

beforeEach(() => {
  client.zoomDisconnect.mockReset()
  toast.mockReset()
})

describe("Zoom disconnect", () => {
  it("explains the consequence and stays connected by default", async () => {
    renderZoom()

    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }))

    const dialog = await screen.findByRole("alertdialog", {
      name: "Disconnect Zoom?",
    })
    expect(dialog).toHaveAccessibleDescription(
      /Cosmas will stop joining your scheduled Zoom meetings/,
    )
    expect(screen.getByRole("button", { name: "Stay connected" })).toHaveFocus()
    fireEvent.click(screen.getByRole("button", { name: "Stay connected" }))

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
    expect(client.zoomDisconnect).not.toHaveBeenCalled()
  })

  it("disconnects after confirmation", async () => {
    client.zoomDisconnect.mockResolvedValue({ data: {}, error: undefined })
    renderZoom()

    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }))
    const dialog = await screen.findByRole("alertdialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }))

    await waitFor(() => expect(client.zoomDisconnect).toHaveBeenCalledOnce())
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith({ title: "Zoom disconnected" }),
    )
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
  })

  it("stays open and explains when disconnecting fails", async () => {
    client.zoomDisconnect.mockResolvedValue({
      data: undefined,
      error: { detail: "x" },
    })
    renderZoom()

    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }))
    const dialog = await screen.findByRole("alertdialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Couldn't disconnect Zoom" }),
      ),
    )
    expect(screen.getByRole("alertdialog")).toBeInTheDocument()
    expect(
      within(dialog).getByRole("button", { name: "Disconnect" }),
    ).toBeEnabled()
  })
})
