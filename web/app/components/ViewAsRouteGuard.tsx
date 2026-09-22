import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react"

import { Loader2, ShieldAlert } from "lucide-react"

import { Button } from "@/components/ui/button"
import AdminBar from "~/components/AdminBar"
import { queryClient } from "~/configuration/tanstack-query"
import {
  type ViewAsStatus,
  type ViewAsStatusCheck,
  checkAuthenticatedViewAsStatus,
  checkViewAsStatus,
  endViewAs,
  viewAsDiagnosticCopy,
} from "~/lib/view-as-api"
import {
  VIEW_AS_HARD_BLOCK_EVENT,
  hardBlockViewAsBinding,
  isViewAsRequestBinding,
  subscribeToViewAsStateChanges,
} from "~/lib/view-as-binding"

type GuardProps = {
  check: ViewAsStatusCheck
  children: ReactNode
  className?: string
}

type ErrorGuardProps = {
  children: ReactNode
  pathname?: string
}

const PROTECTED_ROUTE_PREFIXES = [
  "/onboarding",
  "/notes",
  "/intake",
  "/schedule",
  "/settings",
] as const
const EXTERNAL_IDENTITY_ROUTE_PREFIXES = ["/sign-in", "/sign-up"] as const
const PUBLIC_CAPABILITY_ROUTE_PREFIXES = ["/chat/intake"] as const
export const VIEW_AS_REVALIDATE_INTERVAL_MS = 30_000
const MAX_BROWSER_TIMEOUT_MS = 2_147_483_647
const ViewAsActiveContext = createContext(false)

export function useViewAsActive() {
  return useContext(ViewAsActiveContext)
}

export function requiresViewAsProtection(pathname: string) {
  const normalized = pathname.replace(/\/+$/, "") || "/"
  return PROTECTED_ROUTE_PREFIXES.some(
    (prefix) => normalized === prefix || normalized.startsWith(`${prefix}/`),
  )
}

function exposesExternalIdentityControls(pathname: string) {
  const normalized = pathname.replace(/\/+$/, "") || "/"
  return EXTERNAL_IDENTITY_ROUTE_PREFIXES.some(
    (prefix) => normalized === prefix || normalized.startsWith(`${prefix}/`),
  )
}

function exposesPublicCapability(pathname: string) {
  const normalized = pathname.replace(/\/+$/, "") || "/"
  return PUBLIC_CAPABILITY_ROUTE_PREFIXES.some(
    (prefix) => normalized === prefix || normalized.startsWith(`${prefix}/`),
  )
}

export function requiresStrictPublicViewAsVerification(pathname: string) {
  const normalized = pathname.replace(/\/+$/, "") || "/"
  return [
    ...EXTERNAL_IDENTITY_ROUTE_PREFIXES,
    ...PUBLIC_CAPABILITY_ROUTE_PREFIXES,
  ].some(
    (prefix) => normalized === prefix || normalized.startsWith(`${prefix}/`),
  )
}

function hasAccountIdentity(
  account: ViewAsStatus["actor"] | ViewAsStatus["target"],
) {
  return account !== null && account.doctor_id.length > 0
}

export function hasConsistentViewAsStatus(status: ViewAsStatus) {
  if (status.active) {
    return (
      isViewAsRequestBinding(status.request_binding) &&
      status.request_binding !== "inactive" &&
      status.can_view_as &&
      status.read_only &&
      status.expires_at !== null &&
      Number.isFinite(Date.parse(status.expires_at)) &&
      Date.parse(status.expires_at) > Date.now() &&
      hasAccountIdentity(status.actor) &&
      hasAccountIdentity(status.target)
    )
  }

  return (
    status.request_binding === "inactive" &&
    !status.read_only &&
    status.diagnostic_code === null &&
    status.expires_at === null &&
    status.target === null &&
    (status.can_view_as
      ? hasAccountIdentity(status.actor)
      : status.actor === null)
  )
}

function sameViewAsPrincipal(
  prior: ViewAsStatus | null,
  current: ViewAsStatus,
) {
  if (prior === null || !prior.active || !current.active) return true
  return (
    prior.request_binding === current.request_binding &&
    prior.actor?.doctor_id === current.actor?.doctor_id &&
    prior.target?.doctor_id === current.target?.doctor_id
  )
}

function useLiveViewAsCheck(
  initialCheck: ViewAsStatusCheck,
  readCheck: () => Promise<ViewAsStatusCheck> = checkViewAsStatus,
  revalidateOnMount = false,
) {
  const [resolution, setResolution] = useState<{
    check: ViewAsStatusCheck
    pending: boolean
  }>({ check: initialCheck, pending: revalidateOnMount })
  const lastVerified = useRef<ViewAsStatus | null>(
    initialCheck.state === "ready" ? initialCheck.status : null,
  )
  const latestRequest = useRef(0)
  const inFlight = useRef<number | null>(null)

  useEffect(() => {
    latestRequest.current += 1
    inFlight.current = null
    setResolution({ check: initialCheck, pending: revalidateOnMount })
    lastVerified.current =
      initialCheck.state === "ready" ? initialCheck.status : null
  }, [initialCheck, revalidateOnMount])

  const revalidate = useCallback(
    async (hideImmediately = false) => {
      if (inFlight.current !== null && !hideImmediately) return
      const requestId = latestRequest.current + 1
      latestRequest.current = requestId
      inFlight.current = requestId
      setResolution((current) => ({
        check: hideImmediately
          ? { state: "unavailable", status: null }
          : current.check,
        pending: true,
      }))
      try {
        const next = await readCheck()
        if (latestRequest.current !== requestId) return
        if (
          next.state === "ready" &&
          !sameViewAsPrincipal(lastVerified.current, next.status)
        ) {
          throw hardBlockViewAsBinding()
        }
        if (next.state === "ready") lastVerified.current = next.status
        setResolution({ check: next, pending: false })
      } catch {
        if (latestRequest.current !== requestId) return
        setResolution({
          check: { state: "unavailable", status: null },
          pending: false,
        })
      } finally {
        if (inFlight.current === requestId) inFlight.current = null
      }
    },
    [readCheck],
  )

  useEffect(() => {
    if (revalidateOnMount) void revalidate()
  }, [revalidateOnMount, revalidate])

  useEffect(() => {
    const hardBlock = () => {
      latestRequest.current += 1
      inFlight.current = null
      queryClient.clear()
      setResolution({
        check: { state: "unavailable", status: null },
        pending: false,
      })
    }
    window.addEventListener(VIEW_AS_HARD_BLOCK_EVENT, hardBlock)
    return () => window.removeEventListener(VIEW_AS_HARD_BLOCK_EVENT, hardBlock)
  }, [])

  useEffect(
    () => subscribeToViewAsStateChanges(() => void revalidate(true)),
    [revalidate],
  )

  useEffect(() => {
    const onFocus = () => void revalidate()
    const onVisibility = () => {
      if (document.visibilityState === "visible") void revalidate()
    }
    window.addEventListener("focus", onFocus)
    document.addEventListener("visibilitychange", onVisibility)
    const interval = window.setInterval(
      () => void revalidate(),
      VIEW_AS_REVALIDATE_INTERVAL_MS,
    )
    return () => {
      window.removeEventListener("focus", onFocus)
      document.removeEventListener("visibilitychange", onVisibility)
      window.clearInterval(interval)
    }
  }, [revalidate])

  const expiresAt =
    resolution.check.state === "ready" && resolution.check.status.active
      ? resolution.check.status.expires_at
      : null
  useEffect(() => {
    if (expiresAt === null) return
    const expiresIn = Math.max(0, Date.parse(expiresAt) - Date.now())
    const timeout = window.setTimeout(
      () => void revalidate(true),
      Math.min(expiresIn, MAX_BROWSER_TIMEOUT_MS),
    )
    return () => window.clearTimeout(timeout)
  }, [expiresAt, revalidate])

  return {
    ...resolution,
    lastVerified: lastVerified.current,
    revalidate,
  }
}

const UNKNOWN_VIEW_AS_CHECK: ViewAsStatusCheck = {
  state: "unavailable",
  status: null,
}

function PublicRouteVerificationPending() {
  return (
    <main
      className="flex min-h-screen items-center justify-center"
      role="status"
      aria-label="Checking session security"
    >
      <Loader2
        className="text-primary h-7 w-7 animate-spin"
        aria-hidden="true"
      />
      <span className="sr-only">Loading application</span>
    </main>
  )
}

function PublicRouteVerificationNotice() {
  return (
    <div
      className="text-muted-foreground w-full border-b px-4 py-1 text-center text-xs"
      role="status"
      aria-label="Checking session security"
      data-view-as-verification-notice
    >
      Checking session security…
    </div>
  )
}

function ViewAsPublicRouteGuard({
  children,
  pathname,
}: {
  children: ReactNode
  pathname: string
}) {
  const live = useLiveViewAsCheck(
    UNKNOWN_VIEW_AS_CHECK,
    checkAuthenticatedViewAsStatus,
    true,
  )
  const current =
    live.check.state === "ready" &&
    live.check.status.active &&
    hasConsistentViewAsStatus(live.check.status)
      ? live.check.status
      : null
  const lastActive =
    live.lastVerified?.active && hasConsistentViewAsStatus(live.lastVerified)
      ? live.lastVerified
      : null
  const bannerStatus = current ?? lastActive

  // Once Clerk has established that this browser is signed out, routine
  // revalidation must not remount external identity controls. Clerk prepares
  // one-time-code factors on mount, so replacing an in-progress sign-in or
  // sign-up form can supersede a code the user is still retrieving. Hard
  // revalidation replaces the check with `unavailable` and still blocks here.
  if (
    live.pending &&
    live.lastVerified === null &&
    live.check.state === "signed_out" &&
    exposesExternalIdentityControls(pathname)
  ) {
    return children
  }

  if (live.pending && live.lastVerified === null) {
    if (requiresStrictPublicViewAsVerification(pathname)) {
      return <PublicRouteVerificationPending />
    }
    return (
      <>
        <PublicRouteVerificationNotice />
        {children}
      </>
    )
  }

  if (bannerStatus) {
    const blocksIdentityControls = exposesExternalIdentityControls(pathname)
    const blocksPublicCapability = exposesPublicCapability(pathname)
    return (
      <ViewAsActiveContext.Provider value>
        <AdminBar initialStatus={bannerStatus} />
        {blocksIdentityControls || blocksPublicCapability ? (
          <main
            className="mx-auto w-full max-w-2xl px-6 py-16"
            role="status"
            data-view-as-interstitial
          >
            <p className="text-muted-foreground text-sm font-medium tracking-wide uppercase">
              Read-only support view
            </p>
            <h1 className="mt-3 text-3xl font-semibold">
              {blocksPublicCapability
                ? "Patient intake unavailable"
                : "Account controls unavailable"}
            </h1>
            <p className="text-muted-foreground mt-4 leading-relaxed">
              {blocksPublicCapability
                ? "Exit view-as before opening or changing a patient intake."
                : "Exit view-as before opening sign-in, sign-up, or account-security controls."}
            </p>
          </main>
        ) : (
          children
        )}
      </ViewAsActiveContext.Provider>
    )
  }

  if (live.check.state === "signed_out") return children
  if (
    live.check.state === "ready" &&
    hasConsistentViewAsStatus(live.check.status)
  ) {
    return children
  }

  // A Clerk bootstrap outage must not take down inert public/legal content.
  // Credentialed intake and external identity controls remain fail closed.
  if (!requiresStrictPublicViewAsVerification(pathname)) return children

  return <ViewAsVerificationUnavailable pending={live.pending} />
}

export function ViewAsGlobalRouteGuard({
  children,
  pathname = typeof window === "undefined" ? "/" : window.location.pathname,
}: ErrorGuardProps) {
  if (requiresViewAsProtection(pathname)) return children
  return (
    <ViewAsPublicRouteGuard pathname={pathname}>
      {children}
    </ViewAsPublicRouteGuard>
  )
}

export function ViewAsVerificationUnavailable({
  pending = false,
}: {
  pending?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function exit() {
    setBusy(true)
    setError(null)
    try {
      await endViewAs()
      window.location.replace("/")
    } catch {
      setError("View-as could not be ended. Reload and try again.")
      setBusy(false)
    }
  }

  return (
    <div className="bg-background flex min-h-screen flex-col">
      <div
        className="z-[100003] flex w-full flex-wrap items-center gap-3 border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-950 shadow-sm"
        role="alert"
        data-view-as-banner
      >
        <ShieldAlert className="h-4 w-4 shrink-0" aria-hidden="true" />
        <strong>
          {pending
            ? "Verifying support-view status"
            : "Support-view status unavailable"}
        </strong>
        <span>
          Provider content remains hidden until the session is verified.
        </span>
        <Button
          onClick={() => void exit()}
          disabled={busy}
          size="sm"
          variant="outline"
          className="ml-auto border-amber-500 bg-white"
        >
          Exit view-as
        </Button>
        {error ? <span role="alert">{error}</span> : null}
      </div>
      <main
        className="mx-auto w-full max-w-2xl px-6 py-16"
        role="status"
        data-view-as-interstitial
      >
        <p className="text-muted-foreground text-sm font-medium tracking-wide uppercase">
          {pending ? "Safety check in progress" : "Safety check required"}
        </p>
        <h1 className="mt-3 text-3xl font-semibold">
          {pending
            ? "Verifying support view"
            : "We could not verify support view"}
        </h1>
        <p className="text-muted-foreground mt-4 leading-relaxed">
          Reload to verify the session before viewing provider information. You
          can also exit view-as safely from the banner above.
        </p>
      </main>
    </div>
  )
}

export function ViewAsRouteGuard({ check, children, className }: GuardProps) {
  const live = useLiveViewAsCheck(check)
  const currentCheck = live.check
  if (
    currentCheck.state !== "ready" ||
    !hasConsistentViewAsStatus(currentCheck.status)
  ) {
    return <ViewAsVerificationUnavailable pending={live.pending} />
  }

  const { status } = currentCheck
  if (status.active && status.diagnostic_code) {
    return (
      <ViewAsActiveContext.Provider value>
        <div className={className ?? "bg-background min-h-screen"}>
          <AdminBar initialStatus={status} />
          <main className="mx-auto w-full max-w-2xl px-6 py-16" role="status">
            <p className="text-muted-foreground text-sm font-medium tracking-wide uppercase">
              View-as diagnostic
            </p>
            <h1 className="mt-3 text-3xl font-semibold">Account not ready</h1>
            <p className="text-muted-foreground mt-4 leading-relaxed">
              {viewAsDiagnosticCopy(status.diagnostic_code)} No onboarding, BAA,
              billing, or account form is available while reviewing this state.
            </p>
          </main>
        </div>
      </ViewAsActiveContext.Provider>
    )
  }

  return (
    <ViewAsActiveContext.Provider value={status.active}>
      <div className={className}>
        <AdminBar initialStatus={status} />
        {children}
      </div>
    </ViewAsActiveContext.Provider>
  )
}

/**
 * Root error boundaries sit outside the authenticated layout. Re-check the
 * control-plane status before showing any error page for a provider route so
 * a loader/render failure cannot silently remove an active view-as banner.
 */
export function ViewAsProtectedErrorGuard({
  children,
  pathname = typeof window === "undefined" ? "/" : window.location.pathname,
}: ErrorGuardProps) {
  const protectedRoute = requiresViewAsProtection(pathname)
  const [resolution, setResolution] = useState<{
    pathname: string
    check: ViewAsStatusCheck
  } | null>(null)
  const check = resolution?.pathname === pathname ? resolution.check : null

  useEffect(() => {
    if (!protectedRoute) return
    let cancelled = false
    void checkViewAsStatus().then((result) => {
      if (!cancelled) setResolution({ pathname, check: result })
    })
    return () => {
      cancelled = true
    }
  }, [pathname, protectedRoute])

  if (!protectedRoute) {
    return (
      <ViewAsGlobalRouteGuard pathname={pathname}>
        {children}
      </ViewAsGlobalRouteGuard>
    )
  }
  if (check === null) return <ViewAsVerificationUnavailable pending />

  const guardedChildren = check.status?.active ? (
    <main className="mx-auto w-full max-w-2xl px-6 py-16" role="alert">
      <p className="text-muted-foreground text-sm font-medium tracking-wide uppercase">
        Read-only support view
      </p>
      <h1 className="mt-3 text-3xl font-semibold">Page unavailable</h1>
      <p className="text-muted-foreground mt-4 leading-relaxed">
        This page could not be displayed. Exit view-as or return to the account
        list before trying again.
      </p>
    </main>
  ) : (
    children
  )

  return (
    <ViewAsRouteGuard check={check} className="bg-background min-h-screen">
      {guardedChildren}
    </ViewAsRouteGuard>
  )
}
