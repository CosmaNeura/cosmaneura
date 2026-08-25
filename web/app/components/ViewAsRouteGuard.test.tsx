import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { checkViewAsStatus, getViewAsTargets } from "~/lib/view-as-api"
import { resetViewAsBindingStateForTests } from "~/lib/view-as-binding"

import { ViewAsAccountControl } from "./ViewAsAccountControl"
import {
  VIEW_AS_REVALIDATE_INTERVAL_MS,
  ViewAsRouteGuard,
  requiresStrictPublicViewAsVerification,
  requiresViewAsProtection,
} from "./ViewAsRouteGuard"
import { act, render, screen } from "@testing-library/react"

vi.mock("@clerk/clerk-react", () => ({
  UserButton: () => <button>Clerk account menu</button>,
}))

vi.mock("~/lib/view-as-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("~/lib/view-as-api")>()
  return {
    ...original,
    beginViewAs: vi.fn(),
    checkAuthenticatedViewAsStatus: vi.fn(),
    checkViewAsStatus: vi.fn(),
    endViewAs: vi.fn(),
    getViewAsTargets: vi.fn(),
  }
})

beforeEach(() => {
  resetViewAsBindingStateForTests()
  vi.mocked(getViewAsTargets).mockResolvedValue([])
  vi.mocked(checkViewAsStatus).mockResolvedValue({
    state: "unavailable",
    status: null,
  })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const activeStatus = {
  request_binding:
    "vab1.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" as const,
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
}

const inactiveStatus = {
  request_binding: "inactive" as const,
  can_view_as: true,
  active: false,
  read_only: false,
  diagnostic_code: null,
  expires_at: null,
  actor: {
    doctor_id: "doctor_actor",
    email: "actor@example.com",
  },
  target: null,
}

describe("ViewAsRouteGuard", () => {
  it("hides provider content when view-as status is unavailable", () => {
    render(
      <ViewAsRouteGuard
        check={{ state: "unavailable", status: null }}
        className="min-h-screen"
      >
        <p>Private provider content</p>
      </ViewAsRouteGuard>,
    )

    expect(
      screen.getByText("Support-view status unavailable"),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Exit view-as" }),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Private provider content"),
    ).not.toBeInTheDocument()
  })

  it("fails closed if an active response cannot render the full banner", () => {
    render(
      <ViewAsRouteGuard
        check={{
          state: "ready",
          status: {
            ...activeStatus,
            can_view_as: true,
            active: true,
            read_only: true,
            diagnostic_code: null,
            actor: null,
            target: null,
          },
        }}
      >
        <p>Private provider content</p>
      </ViewAsRouteGuard>,
    )

    expect(
      screen.getByText("Support-view status unavailable"),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Private provider content"),
    ).not.toBeInTheDocument()
  })

  it("keeps the actor, target, and Exit banner above active route content", () => {
    render(
      <ViewAsRouteGuard
        check={{
          state: "ready",
          status: activeStatus,
        }}
      >
        <p>Private provider content</p>
      </ViewAsRouteGuard>,
    )

    expect(
      screen.getByText("actor@example.com → target@example.com"),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Exit view-as" }),
    ).toBeInTheDocument()
    expect(screen.getByText("Private provider content")).toBeInTheDocument()
  })

  it("hides provider content immediately when the local session expires", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-08-24T12:00:00Z"))
    vi.mocked(checkViewAsStatus).mockReturnValue(new Promise(() => {}))

    render(
      <ViewAsRouteGuard
        check={{
          state: "ready",
          status: {
            ...activeStatus,
            expires_at: "2026-08-24T12:00:01Z",
          },
        }}
      >
        <p>Private provider content</p>
      </ViewAsRouteGuard>,
    )

    expect(screen.getByText("Private provider content")).toBeInTheDocument()
    await act(async () => vi.advanceTimersByTime(1_000))
    expect(
      screen.getByText("Verifying support-view status"),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Private provider content"),
    ).not.toBeInTheDocument()
  })

  it("keeps the last verified provider view during periodic revalidation", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-08-24T12:00:00Z"))
    vi.mocked(checkViewAsStatus).mockReturnValue(new Promise(() => {}))

    render(
      <ViewAsRouteGuard check={{ state: "ready", status: activeStatus }}>
        <p>Private provider content</p>
      </ViewAsRouteGuard>,
    )

    await act(async () =>
      vi.advanceTimersByTime(VIEW_AS_REVALIDATE_INTERVAL_MS),
    )
    expect(screen.getByText("Private provider content")).toBeInTheDocument()
    expect(
      screen.queryByText("Verifying support-view status"),
    ).not.toBeInTheDocument()
  })

  it("hides provider content before cross-tab revalidation completes", async () => {
    const listeners = new Set<(event: MessageEvent) => void>()
    class TestBroadcastChannel {
      addEventListener(_type: string, listener: (event: MessageEvent) => void) {
        listeners.add(listener)
      }

      close() {}
    }
    vi.stubGlobal("BroadcastChannel", TestBroadcastChannel)
    vi.mocked(checkViewAsStatus).mockReturnValue(new Promise(() => {}))

    render(
      <ViewAsRouteGuard check={{ state: "ready", status: activeStatus }}>
        <p>Private provider content</p>
      </ViewAsRouteGuard>,
    )

    await act(async () => {
      listeners.forEach((listener) =>
        listener({ data: { type: "state-changed" } } as MessageEvent),
      )
    })
    expect(
      screen.getByText("Verifying support-view status"),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Private provider content"),
    ).not.toBeInTheDocument()
  })

  it("invalidates a stale in-flight check when another tab changes state", async () => {
    const listeners = new Set<(event: MessageEvent) => void>()
    class TestBroadcastChannel {
      addEventListener(_type: string, listener: (event: MessageEvent) => void) {
        listeners.add(listener)
      }

      close() {}
    }
    vi.stubGlobal("BroadcastChannel", TestBroadcastChannel)
    let resolveOld:
      | ((value: { state: "ready"; status: typeof activeStatus }) => void)
      | undefined
    let resolveFresh:
      | ((value: { state: "ready"; status: typeof inactiveStatus }) => void)
      | undefined
    vi.mocked(checkViewAsStatus)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFresh = resolve
          }),
      )

    render(
      <ViewAsRouteGuard check={{ state: "ready", status: activeStatus }}>
        <p>Private provider content</p>
      </ViewAsRouteGuard>,
    )

    await act(async () => window.dispatchEvent(new Event("focus")))
    expect(screen.getByText("Private provider content")).toBeInTheDocument()

    await act(async () => {
      listeners.forEach((listener) =>
        listener({ data: { type: "state-changed" } } as MessageEvent),
      )
    })
    expect(
      screen.getByText("Verifying support-view status"),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Private provider content"),
    ).not.toBeInTheDocument()

    await act(async () =>
      resolveOld?.({ state: "ready", status: activeStatus }),
    )
    expect(
      screen.queryByText("Private provider content"),
    ).not.toBeInTheDocument()

    await act(async () =>
      resolveFresh?.({ state: "ready", status: inactiveStatus }),
    )
    expect(screen.getByText("Private provider content")).toBeInTheDocument()
  })

  it("removes Clerk account controls while view-as is active", () => {
    const { unmount } = render(
      <ViewAsRouteGuard check={{ state: "ready", status: activeStatus }}>
        <ViewAsAccountControl />
      </ViewAsRouteGuard>,
    )

    expect(screen.getByText("Account controls disabled")).toBeInTheDocument()
    expect(screen.queryByText("Clerk account menu")).not.toBeInTheDocument()

    unmount()
    render(<ViewAsAccountControl />)
    expect(screen.getByText("Clerk account menu")).toBeInTheDocument()
  })
})

describe("requiresViewAsProtection", () => {
  it.each([
    "/onboarding",
    "/notes",
    "/notes/note_123",
    "/intake/intake_123",
    "/schedule/meeting_123",
    "/settings",
  ])("protects provider route %s", (pathname) => {
    expect(requiresViewAsProtection(pathname)).toBe(true)
  })

  it.each(["/sign-in", "/support", "/chat/intake/public_123"])(
    "does not turn public route %s into an authenticated route",
    (pathname) => {
      expect(requiresViewAsProtection(pathname)).toBe(false)
    },
  )

  it.each(["/sign-in", "/sign-up/sso-callback", "/chat/intake/public_123"])(
    "requires strict public verification for %s",
    (pathname) => {
      expect(requiresStrictPublicViewAsVerification(pathname)).toBe(true)
    },
  )

  it.each(["/", "/support", "/privacy-policy", "/documentation"])(
    "keeps inert public route %s available after verification failure",
    (pathname) => {
      expect(requiresStrictPublicViewAsVerification(pathname)).toBe(false)
    },
  )
})
