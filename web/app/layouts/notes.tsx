import { Outlet, href } from "react-router"

import { Route } from "./+types/notes"

import { AppSidebar } from "@/components/app-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"
import {
  NOTES_STALE_TIME,
  queryClient,
} from "~/configuration/tanstack-query"
import { appointmentsToSidebarEntries } from "~/lib/appointment"

import { invariant } from "@epic-web/invariant"
import { noteListOptions } from "client/@tanstack/react-query.gen"

// Prefetch through the shared query cache (not a raw noteList() call) so any
// component that later mounts and uses useQuery({...noteListOptions()})
// reads the same cached list instead of issuing a second /internal/v1/notes
// request. Same dedup approach as the authenticated layout uses for settings.
export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  const data = await queryClient.ensureQueryData({
    ...noteListOptions(),
    staleTime: NOTES_STALE_TIME,
  })
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
