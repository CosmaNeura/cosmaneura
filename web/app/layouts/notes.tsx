import { Outlet, href } from "react-router"

import type { Route } from "./+types/notes"

import { AppSidebar } from "@/components/app-sidebar"
import { PracticeAgreementWall } from "@/components/notes/PracticeAgreementWall"
import { SidebarProvider } from "@/components/ui/sidebar"
import { noteList, type Appointment } from "~/configuration/client"
import { queryClient } from "~/configuration/tanstack-query"
import { appointmentsToSidebarEntries } from "~/lib/appointment"
import {
  type PracticeAgreementBlock,
  practiceAgreementBlockFromClientResult,
  practiceAgreementBlockFromRouteFailure,
} from "~/lib/practice-gate-error"

import { invariant } from "@epic-web/invariant"
import { noteListOptions } from "client/@tanstack/react-query.gen"

export type NotesLayoutData = {
  appointments: Appointment[]
  blocked: PracticeAgreementBlock | null
}

function blockedNotesData(blocked: PracticeAgreementBlock): NotesLayoutData {
  return { appointments: [], blocked }
}

export async function loadNotesLayoutData(): Promise<NotesLayoutData> {
  try {
    const result = await noteList()
    const blocked = practiceAgreementBlockFromClientResult(result)
    if (blocked) return blockedNotesData(blocked)

    invariant(result.data, "data failed to load")
    queryClient.setQueryData(noteListOptions().queryKey, result.data)
    return { appointments: result.data.appointments, blocked: null }
  } catch (error) {
    const blocked = practiceAgreementBlockFromRouteFailure(error)
    if (blocked) return blockedNotesData(blocked)
    throw error
  }
}

// Inspect the raw noteList() result so a practice-gate 403 keeps its server
// detail. Successful lists still seed the shared query cache that later
// useQuery({...noteListOptions()}) callers read.
export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  return loadNotesLayoutData()
}

export function ErrorBoundary({ error }: { error: unknown }) {
  const blocked = practiceAgreementBlockFromRouteFailure(error)
  if (blocked) return <PracticeAgreementWall block={blocked} />
  throw error
}

export default function NotesLayout({
  loaderData,
}: {
  loaderData: NotesLayoutData
}) {
  if (loaderData.blocked) {
    return <PracticeAgreementWall block={loaderData.blocked} />
  }

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
