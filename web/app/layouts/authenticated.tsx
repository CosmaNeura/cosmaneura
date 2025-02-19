import { Outlet } from "react-router"

import AdminBar from "~/components/AdminBar"

// This layout assumes the user is authenticated. Any clientLoaders making authenticated
// requests will run an additional check and redirect the user to the login page if it fails.
// Additionally, all authenticated routes will 400 if not auth'd propely.

export default function AuthenticatedLayout() {
  return (
    // if we don't wrap the <Outlet/>, which in most cases is going to be a Sidebar, the sidebar
    // will extend beyond the viewport height. I'm not sure how, but by adding these classes
    // it makes the inner area of the sidebar scrollable.
    <div className="flex h-screen flex-col">
      <AdminBar />
      <Outlet />
    </div>
  )
}
