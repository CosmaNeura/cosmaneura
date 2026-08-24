import { Outlet, href, redirect } from "react-router"

import type { Route } from "./+types/authenticated"

import AdminBar from "~/components/AdminBar"
import { ZoomConnectionAlert } from "~/components/ZoomConnectionAlert"
import {
  SETTINGS_STALE_TIME,
  queryClient,
} from "~/configuration/tanstack-query"

import { settingsDataOptions } from "client/@tanstack/react-query.gen"

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

export function billingRecoveryLocation(requestUrl: string) {
  const current = new URL(requestUrl)
  const params = new URLSearchParams({
    billing: "required",
    returnTo: `${current.pathname}${current.search}`,
  })
  return `${href("/settings")}?${params.toString()}`
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

export async function clientLoader({ request }: Route.ClientLoaderArgs) {
  const data = await queryClient.ensureQueryData({
    ...settingsDataOptions(),
    staleTime: SETTINGS_STALE_TIME,
  })

  if (needsOnboarding(data)) {
    throw redirect(href("/onboarding"))
  }

  if (shouldRedirectToBillingRecovery(data, request.url)) {
    throw redirect(billingRecoveryLocation(request.url))
  }

  return null
}

export default function AuthenticatedLayout() {
  return (
    // if we don't wrap the <Outlet/>, which in most cases is going to be a Sidebar, the sidebar
    // will extend beyond the viewport height. I'm not sure how, but by adding these classes
    // it makes the inner area of the sidebar scrollable.
    <div className="flex h-screen flex-col">
      <AdminBar />
      <ZoomConnectionAlert />
      <Outlet />
    </div>
  )
}
