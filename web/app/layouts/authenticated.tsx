import { Outlet, href, redirect } from "react-router"

import type { Route } from "./+types/authenticated"

import AdminBar from "~/components/AdminBar"
import { ZoomConnectionAlert } from "~/components/ZoomConnectionAlert"
import { settingsData } from "~/configuration/client"

// Guard: any authenticated route redirects to /onboarding until the doctor completes it.
// This runs in parallel with child loaders; if onboarding is incomplete React Router
// cancels the child loaders and performs the redirect.
export async function clientLoader(_: Route.ClientLoaderArgs) {
  const { data } = await settingsData()

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
