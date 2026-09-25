import type { PropsWithChildren } from "react"
import {
  Outlet,
  RouterProvider,
  createMemoryRouter,
  useLoaderData,
  useRouteError,
} from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ErrorBoundary, clientLoader } from "./detail"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const { read } = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock("~/configuration/client", () => ({ noteDetail: read }))
vi.mock("@clerk/clerk-react", () => ({ useUser: () => ({ user: null }) }))
vi.mock("@/components/ui/sidebar", () => ({
  SidebarInset: ({ children }: PropsWithChildren) => <div>{children}</div>,
  SidebarTrigger: () => <button>Toggle sidebar</button>,
}))

function Boundary() {
  return (
    <ErrorBoundary
      {...({ error: useRouteError() } as Parameters<typeof ErrorBoundary>[0])}
    />
  )
}

function LoadedVisit() {
  const value = useLoaderData() as { focusedNoteId: string | null }
  return <div>Visit loaded: {value.focusedNoteId}</div>
}

function openVisit() {
  const router = createMemoryRouter(
    [
      {
        path: "/notes",
        element: <Outlet />,
        children: [
          { index: true, element: <div>Choose a visit</div> },
          {
            path: ":id",
            loader: (args) =>
              clientLoader(args as Parameters<typeof clientLoader>[0]),
            element: <LoadedVisit />,
            errorElement: <Boundary />,
            hydrateFallbackElement: <div>Loading visit</div>,
          },
        ],
      },
    ],
    { initialEntries: ["/notes/synthetic_visit?reviewNote=synthetic_note"] },
  )
  render(<RouterProvider router={router} />)
  return router
}

beforeEach(() => read.mockReset())

describe("visit recovery through the real router", () => {
  it("retries a failed read once, disables duplicate clicks, and restores the requested note", async () => {
    read.mockResolvedValueOnce({
      response: new Response(null, { status: 503 }),
      error: { detail: "Private diagnostic must not appear" },
    })
    let finish!: (value: unknown) => void
    read.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    openVisit()
    expect(
      await screen.findByRole("heading", { name: "Couldn’t load this visit" }),
    ).toBeVisible()
    expect(screen.queryByText(/Private diagnostic/)).not.toBeInTheDocument()
    expect(read).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(
      await screen.findByRole("button", { name: "Trying again…" }),
    ).toBeDisabled()
    expect(read).toHaveBeenCalledTimes(2)
    finish({
      data: { notes: [{ id: "synthetic_note" }] },
      response: new Response(null, { status: 200 }),
    })
    expect(
      await screen.findByText("Visit loaded: synthetic_note"),
    ).toBeVisible()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(read).toHaveBeenCalledTimes(2)
  })

  it.each([403, 404, 422])(
    "offers safe navigation instead of a crash for HTTP %s",
    async (status) => {
      read.mockResolvedValue({
        response: new Response(null, { status }),
        error: { detail: "Other provider record identifiers" },
      })
      const router = openVisit()
      expect(
        await screen.findByRole("heading", { name: "Visit unavailable" }),
      ).toBeVisible()
      expect(screen.queryByText(/Other provider/)).not.toBeInTheDocument()
      fireEvent.click(screen.getByRole("link", { name: "Return to visits" }))
      expect(await screen.findByText("Choose a visit")).toBeVisible()
      await waitFor(() => expect(router.state.location.pathname).toBe("/notes"))
      expect(read).toHaveBeenCalledTimes(1)
    },
  )

  it("keeps unexpected rendering errors for the enclosing error boundary", () => {
    const error = new Error("Synthetic rendering failure")
    expect(() =>
      ErrorBoundary({ error } as Parameters<typeof ErrorBoundary>[0]),
    ).toThrow(error)
  })
})
