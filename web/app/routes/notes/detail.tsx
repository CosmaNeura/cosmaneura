import type { MetaFunction } from "react-router"

import { Route } from "./+types/detail"

import { UploadRecordingButton } from "@/components/notes/UploadRecordingButton"
import { SovereignPanelHost } from "@/components/sovereign/SovereignPanelHost"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import VisitNoteViewer from "~/components/VisitNoteViewer"
import VisitTranscriptViewer from "~/components/VisitTranscriptViewer"
import { EngagementBadge } from "~/components/shared/EngagementBadge"
import type { EngagementRoleKey } from "~/components/shared/EngagementBadge"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { StatusBadge } from "~/components/shared/StatusBadge"
import { noteDetail } from "~/configuration/client"
import type { EngagementRole } from "~/configuration/client"
import { standardDateFormat } from "~/lib/date"
import { isEngagementEnabled } from "~/utils/engagement-feature"

import { useUser } from "@clerk/clerk-react"
import { invariant } from "@epic-web/invariant"

type Status = Parameters<typeof StatusBadge>[0]["status"]

export function EmptyNotes() {
  return (
    <div className="mb-4">
      <div className="mb-2 text-xl font-semibold">Notes</div>
      <div className="text-muted-foreground text-sm italic">
        No Notes Available. Upload some!
      </div>
    </div>
  )
}

export async function clientLoader(loaderArgs: Route.ClientLoaderArgs) {
  const { data } = await noteDetail({
    path: {
      appointment_id: loaderArgs.params.id,
    },
  })

  // TODO need idiomatic error handling here
  invariant(data, "data should exist")

  return data
}

// TODO can we make this dynamic?
export const meta: MetaFunction = () => {
  return [
    { title: "Notetaking" },
    {
      name: "description",
      content: "Your patient meeting notes",
    },
  ]
}

function Header({ title }: { title?: string }) {
  return (
    <header className="bg-background sticky top-0 z-10 flex shrink-0 items-center gap-2 border-b p-4">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <h1 className="text-lg font-semibold">
        {title ? title : "Select a patient"}
      </h1>
      <div className="ml-auto">
        <UploadRecordingButton />
      </div>
    </header>
  )
}

function NoteHeader({
  status,
  title,
  dataFields,
  engagementRole,
}: {
  status: Status
  title: string
  dataFields: Record<string, string>
  engagementRole?: EngagementRole | null
}) {
  return (
    <div className="mb-6 flex items-start justify-between">
      <div className="text-left">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">{title}</h1>
          <StatusBadge status={status} />
        </div>
        {engagementRole ? (
          <div className="mt-2">
            <EngagementBadge
              roleKey={engagementRole.role_key as EngagementRoleKey}
              roleName={engagementRole.role_name}
            />
          </div>
        ) : null}
        <div className="mt-1 space-y-1">
          {Object.entries(dataFields).map(([key, value]) => (
            <p className="text-sm" key={key}>
              <span className="text-muted-foreground font-medium">{key}:</span>{" "}
              <span>{value}</span>
            </p>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function NoteDetailPage({ loaderData }: Route.ComponentProps) {
  const { transcript, notes } = loaderData
  const hasNotes = notes && notes.length > 0

  // The engagement "personality" badge is gated to the same beta cohort as
  // Zoom, so in production only allowlisted doctors see it.
  const { user } = useUser()
  const engagementEnabled = isEngagementEnabled(
    user?.primaryEmailAddress?.emailAddress,
  )

  return (
    <SidebarInset>
      <SovereignPanelHost recordId={loaderData.patient.id} />
      <Header title={loaderData.patient.name} />
      <InnerScrollableContent>
        <NoteHeader
          title={loaderData.patient.name}
          status={hasNotes ? "completed" : "not-started"}
          engagementRole={engagementEnabled ? loaderData.engagement_role : null}
          dataFields={{
            "Appointment Date": standardDateFormat(
              loaderData.appointment.appointment_date,
            ),
            // "Notes Created At": "Completed at",
            // TODO no location in data model 7yet
            // Location: "Denver, CO",
          }}
        />

        {hasNotes ? (
          notes.map((note, index) => (
            <VisitNoteViewer
              key={note.id}
              note={note}
              defaultOpen={index === 0}
            />
          ))
        ) : (
          <EmptyNotes />
        )}

        <VisitTranscriptViewer transcript={transcript} />
      </InnerScrollableContent>
    </SidebarInset>
  )
}
