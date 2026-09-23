import { type MetaFunction, data, isRouteErrorResponse } from "react-router"

import type { Route } from "./+types/detail"

import { UploadRecordingButton } from "@/components/notes/UploadRecordingButton"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { LabResultsPanel } from "~/components/LabResultsPanel"
import { MedicationPanel } from "~/components/MedicationPanel"
import VisitNoteViewer from "~/components/VisitNoteViewer"
import VisitTranscriptViewer from "~/components/VisitTranscriptViewer"
import { EngagementBadge } from "~/components/shared/EngagementBadge"
import type { EngagementRoleKey } from "~/components/shared/EngagementBadge"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { StatusBadge } from "~/components/shared/StatusBadge"
import { noteDetail } from "~/configuration/client"
import type { EngagementRole } from "~/configuration/client"
import { standardDateFormat } from "~/lib/date"
import {
  practiceAgreementBlockFromClientResult,
  practiceAgreementBlockFromUnknown,
} from "~/lib/practice-gate-error"
import { SovereignPanelHost } from "~/sovereign/SovereignPanelHost"
import { isEngagementEnabled } from "~/utils/engagement-feature"
import { isReferenceToolkitEnabled } from "~/utils/reference-toolkit-feature"
import { useTerminology } from "~/utils/terminology"

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
  try {
    const result = await noteDetail({
      throwOnError: false,
      signal: loaderArgs.request.signal,
      path: {
        appointment_id: loaderArgs.params.id,
      },
    })
    const blocked = practiceAgreementBlockFromClientResult(result)
    if (blocked) {
      throw data(blocked, { status: 403 })
    }

    invariant(result.data, "data should exist")
    return result.data
  } catch (error) {
    if (isRouteErrorResponse(error)) throw error
    const blocked = practiceAgreementBlockFromUnknown(error)
    if (blocked) throw data(blocked, { status: 403 })
    throw error
  }
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
  const t = useTerminology()
  return (
    <header className="bg-background sticky top-0 z-10 flex shrink-0 items-center gap-2 border-b p-4">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <h1 className="text-lg font-semibold">
        {title ? title : `Select a ${t.patient}`}
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
  const { transcript, notes, note_transcripts } = loaderData
  const hasNotes = notes && notes.length > 0
  const distinctNoteSources =
    new Set((notes ?? []).map((note) => note.ai_transcript_id)).size > 1
  // The fallback supports a prior backend without attaching its transcript to
  // a note with a different source ID.
  const sourceTranscripts = note_transcripts ?? (transcript ? [transcript] : [])
  const sharedTranscript =
    !hasNotes || notes.every((note) => note.ai_transcript_id === transcript?.id)
      ? transcript
      : null

  // The engagement "personality" badge is gated to the same beta cohort as
  // Zoom, so in production only allowlisted doctors see it.
  const { user } = useUser()
  const engagementEnabled = isEngagementEnabled(
    user?.primaryEmailAddress?.emailAddress,
  )
  const referenceToolkitEnabled = isReferenceToolkitEnabled(
    user?.primaryEmailAddress?.emailAddress,
  )

  return (
    <SidebarInset>
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
              review={loaderData.session_prep_reviews?.[note.id]}
              transcript={
                distinctNoteSources
                  ? (sourceTranscripts.find(
                      (source) => source.id === note.ai_transcript_id,
                    ) ?? null)
                  : undefined
              }
            />
          ))
        ) : (
          <EmptyNotes />
        )}

        {referenceToolkitEnabled && (
          <MedicationPanel patientId={loaderData.patient.id} />
        )}
        {referenceToolkitEnabled && (
          <LabResultsPanel patientId={loaderData.patient.id} />
        )}

        {!distinctNoteSources && (
          <VisitTranscriptViewer transcript={sharedTranscript} />
        )}
      </InnerScrollableContent>
      <SovereignPanelHost appointmentId={loaderData.appointment.id} />
    </SidebarInset>
  )
}
