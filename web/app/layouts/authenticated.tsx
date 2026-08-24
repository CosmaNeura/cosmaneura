import { Outlet, href, redirect } from "react-router"

import type { Route } from "./+types/authenticated"

import AdminBar from "~/components/AdminBar"
import { ZoomConnectionAlert } from "~/components/ZoomConnectionAlert"
import {
  SETTINGS_STALE_TIME,
  queryClient,
} from "~/configuration/tanstack-query"
import { isAccountRecoveryRequired } from "~/lib/account-recovery"
import { billingRecoveryLocation } from "~/lib/billing-recovery"
import { getViewAsStatus, viewAsDiagnosticCopy } from "~/lib/view-as-api"

import { settingsDataOptions } from "client/@tanstack/react-query.gen"

export { billingRecoveryLocation } from "~/lib/billing-recovery"

// Guard: the server's effective access decision is authoritative. It includes
// the normal practice/BAA path and exact-account pilot grants without asking
// the browser to infer authorization from factual practice fields. Admin
// accounts have no practice and skip both checks.
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
  return Boolean(
    data &&
      !data.is_admin &&
      (!data.onboarding_completed || !data.practice_ready),
  )
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
  return Boolean(
    data &&
      !data.is_admin &&
      data.practice_ready &&
      data.billing_recovery_required,
  )
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
  const viewAs = await getViewAsStatus().catch(() => null)
  if (viewAs?.active && viewAs.diagnostic_code) {
    return { viewAs }
  }

  let data
  try {
    data = await queryClient.ensureQueryData({
      ...settingsDataOptions(),
      staleTime: SETTINGS_STALE_TIME,
      retry: shouldRetrySettingsQuery,
    })
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

  return { viewAs }
}

export default function AuthenticatedLayout({
  loaderData,
}: Route.ComponentProps) {
  const viewAs = loaderData?.viewAs ?? null
  if (viewAs?.active && viewAs.diagnostic_code) {
    return (
      <div className="flex h-screen flex-col">
        <AdminBar initialStatus={viewAs} />
        <main className="mx-auto w-full max-w-2xl px-6 py-16" role="status">
          <p className="text-muted-foreground text-sm font-medium tracking-wide uppercase">
            View-as diagnostic
          </p>
          <h1 className="mt-3 text-3xl font-semibold">Account not ready</h1>
          <p className="text-muted-foreground mt-4 leading-relaxed">
            {viewAsDiagnosticCopy(viewAs.diagnostic_code)} No onboarding, BAA,
            billing, or account form is available while reviewing this state.
          </p>
        </main>
      </div>
    )
  }

  return (
    // if we don't wrap the <Outlet/>, which in most cases is going to be a Sidebar, the sidebar
    // will extend beyond the viewport height. I'm not sure how, but by adding these classes
    // it makes the inner area of the sidebar scrollable.
    <div className="flex h-screen flex-col">
      <AdminBar initialStatus={viewAs} />
      <ZoomConnectionAlert />
      <Outlet />
    </div>
  )
}
