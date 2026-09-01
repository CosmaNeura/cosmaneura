import { useEffect } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  type ViewAsStatusCheck,
  checkAuthenticatedViewAsStatus,
  checkViewAsStatus,
  getViewAsTargets,
} from "~/lib/view-as-api"
import { resetViewAsBindingStateForTests } from "~/lib/view-as-binding"

import { ViewAsAccountControl } from "./ViewAsAccountControl"
import {
  VIEW_AS_REVALIDATE_INTERVAL_MS,
  ViewAsGlobalRouteGuard,
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
  vi.mocked(checkAuthenticatedViewAsStatus).mockReset()
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

const signedOutCheck = {
  state: "signed_out" as const,
  status: null,
}

function deferredViewAsCheck() {
  let settle:
    | ((value: ViewAsStatusCheck | PromiseLike<ViewAsStatusCheck>) => void)
    | undefined
  const promise = new Promise<ViewAsStatusCheck>((resolve) => {
    settle = resolve
  })
  return {
    promise,
    resolve(value: ViewAsStatusCheck) {
      settle?.(value)
    },
  }
}

function AuthMountProbe({
  onMount,
  onUnmount,
}: {
  onMount: () => void
  onUnmount: () => void
}) {
  useEffect(() => {
    onMount()
    return () => onUnmount()
  }, [onMount, onUnmount])

  return <p data-testid="auth-mount-probe">Sign-in controls</p>
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

describe("ViewAsGlobalRouteGuard identity controls", () => {
  it.each([
    ["interval", "/sign-in"],
    ["focus", "/sign-in"],
    ["visibility", "/sign-up/sso-callback"],
  ] as const)(
    "keeps the same auth mount during a delayed %s recheck on %s",
    async (trigger, pathname) => {
      vi.useFakeTimers()
      const initial = deferredViewAsCheck()
      const routine = deferredViewAsCheck()
      vi.mocked(checkAuthenticatedViewAsStatus)
        .mockReturnValueOnce(initial.promise)
        .mockReturnValueOnce(routine.promise)
      const onMount = vi.fn()
      const onUnmount = vi.fn()

      render(
        <ViewAsGlobalRouteGuard pathname={pathname}>
          <AuthMountProbe onMount={onMount} onUnmount={onUnmount} />
        </ViewAsGlobalRouteGuard>,
      )

      expect(onMount).not.toHaveBeenCalled()
      await act(async () => initial.resolve(signedOutCheck))
      const authMount = screen.getByTestId("auth-mount-probe")
      expect(onMount).toHaveBeenCalledTimes(1)

      await act(async () => {
        if (trigger === "interval") {
          vi.advanceTimersByTime(VIEW_AS_REVALIDATE_INTERVAL_MS)
        } else if (trigger === "focus") {
          window.dispatchEvent(new Event("focus"))
        } else {
          expect(document.visibilityState).toBe("visible")
          document.dispatchEvent(new Event("visibilitychange"))
        }
      })

      expect(checkAuthenticatedViewAsStatus).toHaveBeenCalledTimes(2)
      expect(screen.getByTestId("auth-mount-probe")).toBe(authMount)
      expect(onMount).toHaveBeenCalledTimes(1)
      expect(onUnmount).not.toHaveBeenCalled()

      await act(async () => routine.resolve(signedOutCheck))
      expect(screen.getByTestId("auth-mount-probe")).toBe(authMount)
      expect(onMount).toHaveBeenCalledTimes(1)
      expect(onUnmount).not.toHaveBeenCalled()
    },
  )

  it("removes auth controls when a routine recheck finds active view-as", async () => {
    vi.useFakeTimers()
    const initial = deferredViewAsCheck()
    const routine = deferredViewAsCheck()
    vi.mocked(checkAuthenticatedViewAsStatus)
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(routine.promise)
    const onMount = vi.fn()
    const onUnmount = vi.fn()

    render(
      <ViewAsGlobalRouteGuard pathname="/sign-in">
        <AuthMountProbe onMount={onMount} onUnmount={onUnmount} />
      </ViewAsGlobalRouteGuard>,
    )
    await act(async () => initial.resolve(signedOutCheck))
    const authMount = screen.getByTestId("auth-mount-probe")

    await act(async () => window.dispatchEvent(new Event("focus")))
    expect(screen.getByTestId("auth-mount-probe")).toBe(authMount)

    await act(async () =>
      routine.resolve({ state: "ready", status: activeStatus }),
    )
    expect(screen.getByText("Account controls unavailable")).toBeInTheDocument()
    expect(screen.queryByTestId("auth-mount-probe")).not.toBeInTheDocument()
    expect(onMount).toHaveBeenCalledTimes(1)
    expect(onUnmount).toHaveBeenCalledTimes(1)
  })

  it("fails closed when a routine auth-route recheck becomes unavailable", async () => {
    vi.useFakeTimers()
    const initial = deferredViewAsCheck()
    const routine = deferredViewAsCheck()
    vi.mocked(checkAuthenticatedViewAsStatus)
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(routine.promise)
    const onMount = vi.fn()
    const onUnmount = vi.fn()

    render(
      <ViewAsGlobalRouteGuard pathname="/sign-in">
        <AuthMountProbe onMount={onMount} onUnmount={onUnmount} />
      </ViewAsGlobalRouteGuard>,
    )
    await act(async () => initial.resolve(signedOutCheck))
    await act(async () => window.dispatchEvent(new Event("focus")))
    expect(screen.getByTestId("auth-mount-probe")).toBeInTheDocument()

    await act(async () =>
      routine.resolve({ state: "unavailable", status: null }),
    )
    expect(
      screen.getByText("Support-view status unavailable"),
    ).toBeInTheDocument()
    expect(screen.queryByTestId("auth-mount-probe")).not.toBeInTheDocument()
    expect(onUnmount).toHaveBeenCalledTimes(1)
  })

  it("removes auth controls immediately when another tab changes view-as", async () => {
    vi.useFakeTimers()
    const listeners = new Set<(event: MessageEvent) => void>()
    class TestBroadcastChannel {
      addEventListener(_type: string, listener: (event: MessageEvent) => void) {
        listeners.add(listener)
      }

      close() {}
    }
    vi.stubGlobal("BroadcastChannel", TestBroadcastChannel)
    const initial = deferredViewAsCheck()
    const hardRecheck = deferredViewAsCheck()
    vi.mocked(checkAuthenticatedViewAsStatus)
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(hardRecheck.promise)
    const onMount = vi.fn()
    const onUnmount = vi.fn()

    render(
      <ViewAsGlobalRouteGuard pathname="/sign-in">
        <AuthMountProbe onMount={onMount} onUnmount={onUnmount} />
      </ViewAsGlobalRouteGuard>,
    )
    await act(async () => initial.resolve(signedOutCheck))

    await act(async () => {
      listeners.forEach((listener) =>
        listener({ data: { type: "state-changed" } } as MessageEvent),
      )
    })
    expect(
      screen.getByRole("status", { name: "Checking session security" }),
    ).toBeInTheDocument()
    expect(screen.queryByTestId("auth-mount-probe")).not.toBeInTheDocument()
    expect(onUnmount).toHaveBeenCalledTimes(1)

    await act(async () =>
      hardRecheck.resolve({ state: "ready", status: activeStatus }),
    )
    expect(screen.getByText("Account controls unavailable")).toBeInTheDocument()
  })

  it("does not let a stale routine result undo a newer hard recheck", async () => {
    vi.useFakeTimers()
    const listeners = new Set<(event: MessageEvent) => void>()
    class TestBroadcastChannel {
      addEventListener(_type: string, listener: (event: MessageEvent) => void) {
        listeners.add(listener)
      }

      close() {}
    }
    vi.stubGlobal("BroadcastChannel", TestBroadcastChannel)
    const initial = deferredViewAsCheck()
    const routine = deferredViewAsCheck()
    const hardRecheck = deferredViewAsCheck()
    vi.mocked(checkAuthenticatedViewAsStatus)
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(routine.promise)
      .mockReturnValueOnce(hardRecheck.promise)

    render(
      <ViewAsGlobalRouteGuard pathname="/sign-in">
        <p data-testid="auth-mount-probe">Sign-in controls</p>
      </ViewAsGlobalRouteGuard>,
    )
    await act(async () => initial.resolve(signedOutCheck))
    await act(async () => window.dispatchEvent(new Event("focus")))
    expect(screen.getByTestId("auth-mount-probe")).toBeInTheDocument()

    await act(async () => {
      listeners.forEach((listener) =>
        listener({ data: { type: "state-changed" } } as MessageEvent),
      )
    })
    expect(screen.queryByTestId("auth-mount-probe")).not.toBeInTheDocument()

    await act(async () => routine.resolve(signedOutCheck))
    expect(screen.queryByTestId("auth-mount-probe")).not.toBeInTheDocument()

    await act(async () =>
      hardRecheck.resolve({ state: "ready", status: activeStatus }),
    )
    expect(screen.getByText("Account controls unavailable")).toBeInTheDocument()
  })

  it("keeps public patient intake fail-closed during a delayed recheck", async () => {
    vi.useFakeTimers()
    const initial = deferredViewAsCheck()
    const routine = deferredViewAsCheck()
    vi.mocked(checkAuthenticatedViewAsStatus)
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(routine.promise)

    render(
      <ViewAsGlobalRouteGuard pathname="/chat/intake/intake_private">
        <p data-testid="public-intake">Patient intake controls</p>
      </ViewAsGlobalRouteGuard>,
    )
    await act(async () => initial.resolve(signedOutCheck))
    expect(screen.getByTestId("public-intake")).toBeInTheDocument()

    await act(async () => window.dispatchEvent(new Event("focus")))
    expect(
      screen.getByRole("status", { name: "Checking session security" }),
    ).toBeInTheDocument()
    expect(screen.queryByTestId("public-intake")).not.toBeInTheDocument()
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
