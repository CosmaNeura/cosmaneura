import { Outlet, href, redirect } from "react-router"

import type { Route } from "./+types/authenticated"

import AdminBar from "~/components/AdminBar"
import { ZoomConnectionAlert } from "~/components/ZoomConnectionAlert"
import { SETTINGS_STALE_TIME, queryClient } from "~/configuration/tanstack-query"

import { settingsDataOptions } from "client/@tanstack/react-query.gen"

// Guard: any authenticated route redirects to /onboarding until the doctor completes it.
// This runs in parallel with child loaders; if onboarding is incomplete React Router
// cancels the child loaders and performs the redirect.
//
// Prefetch through the shared query cache (not a raw settingsData() call) so the
// ZoomConnectionAlert below — mounted on every authenticated route — reads the same
// cached settings instead of issuing a second /internal/v1/settings request.
export async function clientLoader(_: Route.ClientLoaderArgs) {
  const data = await queryClient.ensureQueryData({
    ...settingsDataOptions(),
    staleTime: SETTINGS_STALE_TIME,
  })

  if (data && !data.onboarding_completed) {
    throw redirect(href("/onboarding"))
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
