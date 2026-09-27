import {
  Outlet,
  data,
  href,
  isRouteErrorResponse,
  redirect,
  useRevalidator,
} from "react-router"

import type { Route } from "./+types/authenticated"

import { Button } from "@/components/ui/button"
import {
  ViewAsProtectedErrorGuard,
  ViewAsRouteGuard,
} from "~/components/ViewAsRouteGuard"
import { ZoomConnectionAlert } from "~/components/ZoomConnectionAlert"
import {
  SETTINGS_STALE_TIME,
  queryClient,
} from "~/configuration/tanstack-query"
import { isAccountRecoveryRequired } from "~/lib/account-recovery"
import { billingRecoveryLocation } from "~/lib/billing-recovery"
import { ReadDeadlineError, withReadDeadline } from "~/lib/read-deadline"
import { checkViewAsStatus, getViewAsStatus } from "~/lib/view-as-api"

import { settingsDataOptions } from "client/@tanstack/react-query.gen"

export { billingRecoveryLocation } from "~/lib/billing-recovery"

// Guard: the server's effective access decision is authoritative. It includes
// the normal practice/BAA path and exact-account pilot grants without asking
// the browser to infer authorization from factual practice fields. Admin
// Administrative capability does not imply customer-data access; only these
// effective access facts may bypass onboarding or billing recovery.
//
// Prefetch through the shared query cache (not a raw settingsData() call) so the
// ZoomConnectionAlert below — mounted on every authenticated route — reads the same
// cached settings instead of issuing a second /internal/v1/settings request.
export function needsOnboarding(
  data:
    | {
        is_admin: boolean
        onboarding_completed: boolean
        practice_ready: boolean
      }
    | null
    | undefined,
) {
  return Boolean(data && (!data.onboarding_completed || !data.practice_ready))
}

export function needsBillingRecovery(
  data:
    | {
        is_admin: boolean
        practice_ready: boolean
        billing_recovery_required: boolean
      }
    | null
    | undefined,
) {
  return Boolean(data && data.practice_ready && data.billing_recovery_required)
}

export function shouldRedirectToBillingRecovery(
  data: Parameters<typeof needsBillingRecovery>[0],
  requestUrl: string,
) {
  return (
    needsBillingRecovery(data) &&
    new URL(requestUrl).pathname !== href("/settings")
  )
}

export function shouldRetrySettingsQuery(failureCount: number, error: unknown) {
  // An identity collision is durable until support rebinds the account. Do not
  // leave the user behind React Query's exponential retry delay before routing
  // them to the recovery screen. Preserve the normal retry budget otherwise.
  return !isAccountRecoveryRequired(error) && failureCount < 3
}

export async function clientLoader({ request }: Route.ClientLoaderArgs) {
  const settings = settingsDataOptions()
  let settingsStarted = false
  try {
    return await withReadDeadline(
      (signal) =>
        loadAuthenticatedData(request, signal, () => {
          settingsStarted = true
        }),
      {
        signal: request.signal,
        onTimeout: () => {
          // Cancel only the timed-out shared read, fencing its late cache write.
          if (settingsStarted)
            void queryClient.cancelQueries({
              queryKey: settings.queryKey,
              exact: true,
            })
        },
      },
    )
  } catch (error) {
    if (error instanceof ReadDeadlineError) {
      // Settings is part of the access decision too. Never expose the outlet
      // when either settings or support-view verification could not complete.
      throw data({ authenticatedLoadFailure: "temporary" }, { status: 503 })
    }
    throw error
  }
}

function AuthenticatedLoadRecovery() {
  const revalidator = useRevalidator()
  const pending = revalidator.state === "loading"
  return (
    <ViewAsProtectedErrorGuard>
      <main className="mx-auto w-full max-w-lg px-6 py-12" role="alert">
        <h1 className="text-xl font-semibold">
          Couldn’t verify account access
        </h1>
        <p className="text-muted-foreground mt-3">
          Check your connection and try again to open your dashboard.
        </p>
        <Button
          className="mt-6"
          disabled={pending}
          onClick={() => void revalidator.revalidate()}
        >
          {pending ? "Trying again…" : "Try again"}
        </Button>
      </main>
    </ViewAsProtectedErrorGuard>
  )
}

export function ErrorBoundary({ error }: { error: unknown }) {
  if (
    isRouteErrorResponse(error) &&
    error.status === 503 &&
    error.data?.authenticatedLoadFailure === "temporary"
  ) {
    return <AuthenticatedLoadRecovery />
  }
  throw error
}

async function loadAuthenticatedData(
  request: Request,
  signal: AbortSignal,
  onSettingsRead: () => void,
) {
  const viewAsCheck = await checkViewAsStatus(() => getViewAsStatus(signal))
  signal.throwIfAborted()
  if (viewAsCheck.state !== "ready") {
    return { viewAsCheck }
  }
  if (viewAsCheck.status.active && viewAsCheck.status.diagnostic_code) {
    return { viewAsCheck }
  }

  let data
  try {
    onSettingsRead()
    data = await queryClient.ensureQueryData({
      ...settingsDataOptions(),
      staleTime: SETTINGS_STALE_TIME,
      retry: shouldRetrySettingsQuery,
    })
    signal.throwIfAborted()
  } catch (error) {
    if (isAccountRecoveryRequired(error)) {
      throw redirect(href("/onboarding"))
    }
    throw error
  }

  if (needsOnboarding(data)) {
    throw redirect(href("/onboarding"))
  }

  if (shouldRedirectToBillingRecovery(data, request.url)) {
    throw redirect(billingRecoveryLocation(request.url))
  }

  return { viewAsCheck }
}

export default function AuthenticatedLayout({
  loaderData,
}: Route.ComponentProps) {
  return (
    // if we don't wrap the <Outlet/>, which in most cases is going to be a Sidebar, the sidebar
    // will extend beyond the viewport height. I'm not sure how, but by adding these classes
    // it makes the inner area of the sidebar scrollable.
    <ViewAsRouteGuard
      check={loaderData.viewAsCheck}
      className="flex h-screen flex-col"
    >
      <ZoomConnectionAlert />
      <Outlet />
    </ViewAsRouteGuard>
  )
}
