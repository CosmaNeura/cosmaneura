import { type MetaFunction, isRouteErrorResponse } from "react-router"

import type { Route } from "./+types/detail"

import { CaptureNoteReadiness } from "@/components/notes/CaptureNoteReadiness"
import { NoteDetailRecovery } from "@/components/notes/NoteDetailRecovery"
import { SidebarInset } from "@/components/ui/sidebar"
import { LabResultsPanel } from "~/components/LabResultsPanel"
import { MedicationPanel } from "~/components/MedicationPanel"
import { SessionPrepPriorNotes } from "~/components/SessionPrepPriorNotes"
import VisitNoteViewer from "~/components/VisitNoteViewer"
import VisitTranscriptViewer from "~/components/VisitTranscriptViewer"
import { Header } from "~/components/notes/Header"
import { EngagementBadge } from "~/components/shared/EngagementBadge"
import type { EngagementRoleKey } from "~/components/shared/EngagementBadge"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { StatusBadge } from "~/components/shared/StatusBadge"
import type { EngagementRole } from "~/configuration/client"
import { appointmentDateFormat } from "~/lib/date"
import { loadNoteDetail } from "~/lib/note-detail-load"
import { SovereignPanelHost } from "~/sovereign/SovereignPanelHost"
import { isEngagementEnabled } from "~/utils/engagement-feature"
import {
  isReferenceLabsEnabled,
  isReferenceToolkitEnabled,
} from "~/utils/reference-toolkit-feature"

import { useUser } from "@clerk/clerk-react"

type Status = Parameters<typeof StatusBadge>[0]["status"]

export function EmptyNotes({
  captureRequired = false,
}: {
  captureRequired?: boolean
}) {
  return (
    <div className="mb-4">
      <div className="mb-2 text-xl font-semibold">Notes</div>
      <div className="text-muted-foreground text-sm italic">
        {captureRequired
          ? "This visit was started in Scribe. Reopen it in Scribe to resume the recording and prepare its note. A recording uploaded separately cannot generate a note for this visit. Use a new visit for a separate upload."
          : "No Notes Available. Upload some!"}
      </div>
    </div>
  )
}

export async function clientLoader(loaderArgs: Route.ClientLoaderArgs) {
  const result = await loadNoteDetail(loaderArgs.params.id, loaderArgs.request)
  const requestedNoteId = new URL(loaderArgs.request.url).searchParams.get(
    "reviewNote",
  )
  return {
    ...result,
    focusedNoteId: result.notes?.some((note) => note.id === requestedNoteId)
      ? requestedNoteId
      : null,
  }
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  if (isRouteErrorResponse(error)) {
    const kind = error.data?.noteLoadFailure
    if (
      (error.status === 404 && kind === "unavailable") ||
      (error.status === 401 && kind === "session") ||
      (error.status === 503 && kind === "temporary")
    ) {
      return <NoteDetailRecovery kind={kind} />
    }
  }
  // Existing practice/view-as recovery and unexpected render errors retain
  // their enclosing guards; only this loader's safe failures are handled here.
  throw error
}

// TODO can we make this dynamic?
export const meta: MetaFunction = () => {
  return [
    { title: "Notetaking" },
    {
      name: "description",
      content: "Your visit notes",
    },
  ]
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
          <h1
            className="text-2xl font-semibold"
            data-ph-no-capture=""
            data-sentry-block=""
          >
            {title}
          </h1>
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
  const referenceLabsEnabled = isReferenceLabsEnabled(
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
            "Appointment Date": appointmentDateFormat(
              loaderData.appointment.appointment_date,
            ),
            // "Notes Created At": "Completed at",
            // TODO no location in data model 7yet
            // Location: "Denver, CO",
          }}
        />

        {loaderData.session_prep_prior_notes && !loaderData.focusedNoteId && (
          <SessionPrepPriorNotes
            overview={loaderData.session_prep_prior_notes}
          />
        )}

        {hasNotes ? (
          notes.map((note, index) => (
            <VisitNoteViewer
              key={note.id}
              note={note}
              defaultOpen={
                loaderData.focusedNoteId
                  ? loaderData.focusedNoteId === note.id
                  : index === 0
              }
              focusOnMount={loaderData.focusedNoteId === note.id}
              review={loaderData.session_prep_reviews?.[note.id]}
              billingSource={loaderData.billing_header_sources?.[note.id]}
              transcript={
                distinctNoteSources
                  ? (sourceTranscripts.find(
                      (source) => source.id === note.ai_transcript_id,
                    ) ?? null)
                  : undefined
              }
            />
          ))
        ) : loaderData.capture_workflow_required && transcript ? (
          <CaptureNoteReadiness key={loaderData.appointment.id} />
        ) : (
          <EmptyNotes captureRequired={loaderData.capture_workflow_required} />
        )}

        {referenceToolkitEnabled && (
          <MedicationPanel patientId={loaderData.patient.id} />
        )}
        {referenceLabsEnabled && (
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
