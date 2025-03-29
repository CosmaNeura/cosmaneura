import { Outlet, href } from "react-router"

import { Route } from "./+types/notes"

import { AppSidebar } from "@/components/app-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"
import { noteList } from "~/configuration/client"
import {
  appointmentToSidebarEntry,
  appointmentsToSidebarEntries,
} from "~/lib/appointment"

import { invariant } from "@epic-web/invariant"

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  // TODO need idiomatic error handling here
  const { data } = await noteList()
  invariant(data, "data failed to load")

  return data
}

// import type { ShouldRevalidateFunctionArgs } from "react-router"
// TODO not operational! https://github.com/remix-run/react-router/issues/12740
// https://discord.com/channels/770287896669978684/770287896669978687/1328764904894758954
// export function shouldRevalidate(_arg: ShouldRevalidateFunctionArgs) {
//   return false
// }

export default function NotesLayout({ loaderData }: Route.ComponentProps) {
  const { appointments } = loaderData

  return (
    <SidebarProvider
      style={
        {
          "--sidebar-width": "300px",
        } as React.CSSProperties
      }
    >
      <AppSidebar
        isLoading={!appointments}
        header={"Notes"}
        entries={appointmentsToSidebarEntries(appointments, (id: string) =>
          href("/notes/:id", { id }),
        )}
      />
      <Outlet />
    </SidebarProvider>
  )
}
