import type { ComponentType } from "react"
import { RouterProvider, createMemoryRouter } from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { BillingRecoveryRedirectError } from "~/lib/billing-recovery"
import {
  checkAuthenticatedViewAsStatus,
  checkViewAsStatus,
} from "~/lib/view-as-api"

import App, {
  ErrorBoundary,
  HYDRATE_FALLBACK_TIMEOUT_MS,
  HydrateFallback,
} from "./root"
import { act, render, screen, waitFor } from "@testing-library/react"

vi.mock("~/lib/view-as-api", () => ({
  beginViewAs: vi.fn(),
  checkAuthenticatedViewAsStatus: vi.fn(),
  checkViewAsStatus: vi.fn(),
  endViewAs: vi.fn(),
  getViewAsTargets: vi.fn(),
  viewAsDiagnosticCopy: vi.fn(() => "Account not ready."),
}))

const activeStatus = {
  request_binding:
    "vab1.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" as const,
  can_view_as: true,
  active: true,
  read_only: true,
  diagnostic_code: null,
  expires_at: "2099-08-24T17:15:00Z",
  actor: { doctor_id: "doctor_actor", email: "actor@example.com" },
  target: { doctor_id: "doctor_target", email: "target@example.com" },
}

beforeEach(() => {
  vi.mocked(checkAuthenticatedViewAsStatus).mockResolvedValue({
    state: "signed_out",
    status: null,
  })
})

vi.mock("~/configuration", () => ({
  withProviders: (Component: ComponentType) => Component,
}))

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  window.history.replaceState({}, "", "/")
})

describe("Root App", () => {
  it("renders without crashing", async () => {
    const router = createMemoryRouter([
      {
        path: "/",
        element: <App />,
        children: [
          {
            path: "/",
            element: <div>Test content</div>,
          },
        ],
      },
    ])

    render(<RouterProvider router={router} />)
    await waitFor(() =>
      expect(
        screen.queryByRole("status", { name: "Checking session security" }),
      ).not.toBeInTheDocument(),
    )
    expect(screen.getByText("Test content")).toBeInTheDocument()
  })

  it("keeps inert public content usable while session status resolves", () => {
    vi.mocked(checkAuthenticatedViewAsStatus).mockReturnValue(
      new Promise(() => {}),
    )
    const router = createMemoryRouter([
      {
        path: "/",
        element: <App />,
        children: [{ index: true, element: <p>Public content</p> }],
      },
    ])

    render(<RouterProvider router={router} />)

    expect(
      screen.getByRole("status", { name: "Checking session security" }),
    ).toBeInTheDocument()
    expect(screen.getByText("Public content")).toBeInTheDocument()
  })

  it("does not expose strict public controls before session status resolves", () => {
    vi.mocked(checkAuthenticatedViewAsStatus).mockReturnValue(
      new Promise(() => {}),
    )
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <App />,
          children: [{ path: "/sign-in", element: <p>Sign-in controls</p> }],
        },
      ],
      { initialEntries: ["/sign-in"] },
    )

    render(<RouterProvider router={router} />)

    expect(
      screen.getByRole("status", { name: "Checking session security" }),
    ).toBeInTheDocument()
    expect(screen.queryByText("Sign-in controls")).not.toBeInTheDocument()
  })

  it("keeps inert public content available after a Clerk bootstrap outage", async () => {
    vi.mocked(checkAuthenticatedViewAsStatus).mockResolvedValue({
      state: "unavailable",
      status: null,
    })
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <App />,
          children: [{ path: "/support", element: <p>Public support</p> }],
        },
      ],
      { initialEntries: ["/support"] },
    )

    render(<RouterProvider router={router} />)

    await waitFor(() =>
      expect(
        screen.queryByRole("status", { name: "Checking session security" }),
      ).not.toBeInTheDocument(),
    )
    expect(screen.getByText("Public support")).toBeInTheDocument()
    expect(
      screen.queryByText("Support-view status unavailable"),
    ).not.toBeInTheDocument()
  })

  it.each(["/sign-in", "/sign-up", "/chat/intake/intake_private"])(
    "keeps credential-bearing public route %s fail closed on bootstrap failure",
    async (pathname) => {
      vi.mocked(checkAuthenticatedViewAsStatus).mockResolvedValue({
        state: "unavailable",
        status: null,
      })
      const router = createMemoryRouter(
        [
          {
            path: "/",
            element: <App />,
            children: [{ path: "*", element: <p>Credential controls</p> }],
          },
        ],
        { initialEntries: [pathname] },
      )

      render(<RouterProvider router={router} />)

      expect(
        await screen.findByText("Support-view status unavailable"),
      ).toBeInTheDocument()
      expect(screen.queryByText("Credential controls")).not.toBeInTheDocument()
    },
  )

  it("retains the active actor, target, and Exit banner on public routes", async () => {
    vi.mocked(checkAuthenticatedViewAsStatus).mockResolvedValue({
      state: "ready",
      status: activeStatus,
    })
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <App />,
          children: [{ path: "/support", element: <p>Public support</p> }],
        },
      ],
      { initialEntries: ["/support"] },
    )

    render(<RouterProvider router={router} />)

    expect(
      await screen.findByText("actor@example.com → target@example.com"),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Exit view-as" }),
    ).toBeInTheDocument()
    expect(screen.getByText("Public support")).toBeInTheDocument()
  })

  it("never renders an interactive patient intake during active view-as", async () => {
    vi.mocked(checkAuthenticatedViewAsStatus).mockResolvedValue({
      state: "ready",
      status: activeStatus,
    })
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <App />,
          children: [
            {
              path: "/chat/intake/:id",
              element: <p>Private intake composer</p>,
            },
          ],
        },
      ],
      { initialEntries: ["/chat/intake/intake_private"] },
    )

    render(<RouterProvider router={router} />)

    expect(
      await screen.findByText("Patient intake unavailable"),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Private intake composer"),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Exit view-as" }),
    ).toBeInTheDocument()
  })

  it("unmounts a loaded intake when another tab begins view-as", async () => {
    const listeners = new Set<(event: MessageEvent) => void>()
    class TestBroadcastChannel {
      addEventListener(_type: string, listener: (event: MessageEvent) => void) {
        listeners.add(listener)
      }

      close() {}
    }
    vi.stubGlobal("BroadcastChannel", TestBroadcastChannel)
    vi.mocked(checkAuthenticatedViewAsStatus)
      .mockResolvedValueOnce({
        state: "ready",
        status: {
          ...activeStatus,
          request_binding: "inactive",
          active: false,
          read_only: false,
          expires_at: null,
          target: null,
        },
      })
      .mockResolvedValue({ state: "ready", status: activeStatus })
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <App />,
          children: [
            {
              path: "/chat/intake/:id",
              element: <p>Private intake composer</p>,
            },
          ],
        },
      ],
      { initialEntries: ["/chat/intake/intake_private"] },
    )

    render(<RouterProvider router={router} />)
    expect(
      await screen.findByText("Private intake composer"),
    ).toBeInTheDocument()

    await act(async () => {
      listeners.forEach((listener) =>
        listener({ data: { type: "state-changed" } } as MessageEvent),
      )
    })

    expect(
      await screen.findByText("Patient intake unavailable"),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Private intake composer"),
    ).not.toBeInTheDocument()
  })

  it.each(["/", "/sign-in"])(
    "retains the active banner on %s",
    async (pathname) => {
      vi.mocked(checkAuthenticatedViewAsStatus).mockResolvedValue({
        state: "ready",
        status: activeStatus,
      })
      const router = createMemoryRouter(
        [
          {
            path: "/",
            element: <App />,
            children: [
              { index: true, element: <p>Root route</p> },
              { path: "/sign-in", element: <p>Sign in route</p> },
            ],
          },
        ],
        { initialEntries: [pathname] },
      )

      render(<RouterProvider router={router} />)

      expect(
        await screen.findByText("actor@example.com → target@example.com"),
      ).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: "Exit view-as" }),
      ).toBeInTheDocument()
      if (pathname === "/sign-in") {
        expect(screen.queryByText("Sign in route")).not.toBeInTheDocument()
        expect(
          screen.getByText("Account controls unavailable"),
        ).toBeInTheDocument()
      }
    },
  )
})

describe("HydrateFallback", () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it("keeps the initial loading indicator before the delay", () => {
    vi.useFakeTimers()
    render(<HydrateFallback />)

    expect(screen.getByText("Loading application...")).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(HYDRATE_FALLBACK_TIMEOUT_MS - 1)
    })
    expect(screen.getByText("Loading application...")).toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it.each(["/sign-in", "/sign-up", "/notes", "/settings"])(
    "offers neutral loading guidance on %s without declaring sign-in failure",
    (path) => {
      window.history.replaceState({}, "", path)
      vi.useFakeTimers()
      render(<HydrateFallback />)

      act(() => {
        vi.advanceTimersByTime(HYDRATE_FALLBACK_TIMEOUT_MS)
      })

      expect(
        screen.queryByText("Loading application..."),
      ).not.toBeInTheDocument()
      expect(screen.getByRole("status")).toHaveTextContent(
        "The application is taking longer than usual to load.",
      )
      expect(screen.queryByRole("alert")).not.toBeInTheDocument()
      expect(screen.queryByText(/sign-in didn.t load/i)).not.toBeInTheDocument()
    },
  )

  it("clears the delay timer when loading completes", () => {
    vi.useFakeTimers()
    const { unmount } = render(<HydrateFallback />)
    expect(vi.getTimerCount()).toBe(1)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("refreshes only when the user chooses the control", () => {
    vi.useFakeTimers()
    const reload = vi.fn()
    vi.stubGlobal("location", { reload })
    render(<HydrateFallback />)

    act(() => {
      vi.advanceTimersByTime(HYDRATE_FALLBACK_TIMEOUT_MS)
    })
    expect(reload).not.toHaveBeenCalled()
    screen.getByRole("button", { name: "Refresh" }).click()

    expect(reload).toHaveBeenCalledTimes(1)
  })
})

describe("root error boundary", () => {
  it("renders a non-error fallback while billing recovery redirects", async () => {
    const error = new BillingRecoveryRedirectError(
      "/settings?billing=required&returnTo=%2Fnotes",
    )
    const props = { error } as Parameters<typeof ErrorBoundary>[0]

    render(ErrorBoundary(props))

    await waitFor(() =>
      expect(
        screen.queryByRole("status", { name: "Checking session security" }),
      ).not.toBeInTheDocument(),
    )
    expect(
      screen.getByText(/redirecting to restore practice access/i),
    ).toBeInTheDocument()
    expect(screen.queryByText("Oops!")).not.toBeInTheDocument()
  })

  it("retains the normal boundary for unrelated failures", async () => {
    const props = { error: new Error("unrelated") } as Parameters<
      typeof ErrorBoundary
    >[0]

    render(ErrorBoundary(props))

    await waitFor(() =>
      expect(
        screen.queryByRole("status", { name: "Checking session security" }),
      ).not.toBeInTheDocument(),
    )
    expect(screen.getByText("Oops!")).toBeInTheDocument()
  })

  it("hides protected-route error details when status verification fails", async () => {
    window.history.replaceState({}, "", "/notes/note_123")
    vi.mocked(checkViewAsStatus).mockResolvedValue({
      state: "unavailable",
      status: null,
    })
    const props = {
      error: new Error("Private provider detail"),
    } as Parameters<typeof ErrorBoundary>[0]

    render(ErrorBoundary(props))

    expect(
      await screen.findByText("Support-view status unavailable"),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Private provider detail"),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Exit view-as" }),
    ).toBeInTheDocument()
  })

  it("retains the full active banner on protected-route errors", async () => {
    window.history.replaceState({}, "", "/schedule/meeting_123")
    vi.mocked(checkViewAsStatus).mockResolvedValue({
      state: "ready",
      status: activeStatus,
    })
    const props = {
      error: new Error("Private provider detail"),
    } as Parameters<typeof ErrorBoundary>[0]

    render(ErrorBoundary(props))

    expect(
      await screen.findByText("actor@example.com → target@example.com"),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Exit view-as" }),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Private provider detail"),
    ).not.toBeInTheDocument()
    expect(screen.getByText("Page unavailable")).toBeInTheDocument()
  })

  it("retains the active banner on an unknown public error route", async () => {
    window.history.replaceState({}, "", "/unknown")
    vi.mocked(checkAuthenticatedViewAsStatus).mockResolvedValue({
      state: "ready",
      status: activeStatus,
    })
    const props = { error: new Error("Unknown route") } as Parameters<
      typeof ErrorBoundary
    >[0]

    render(ErrorBoundary(props))

    expect(
      await screen.findByText("actor@example.com → target@example.com"),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Exit view-as" }),
    ).toBeInTheDocument()
  })
})
