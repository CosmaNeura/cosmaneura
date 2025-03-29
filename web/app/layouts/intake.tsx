import { Outlet, href } from "react-router"

import { Route } from "./+types/intake"

import { AppSidebar } from "@/components/app-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"
import { intakeList } from "~/configuration/client"
import { appointmentsToSidebarEntries } from "~/lib/appointment"

import { invariant } from "@epic-web/invariant"

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  const { data } = await intakeList()
  invariant(data, "data failed to load")

  return data
}

export default function Layout({ loaderData }: Route.ComponentProps) {
  const { appointments } = loaderData

  return (
    <SidebarProvider
      style={
        {
          "--sidebar-width": "350px",
        } as React.CSSProperties
      }
    >
      <AppSidebar
        isLoading={!appointments}
        header={"Notes"}
        entries={appointmentsToSidebarEntries(appointments, (id: string) =>
          href("/intake/:id", { id }),
        )}
      />
      <Outlet />
    </SidebarProvider>
  )
}
