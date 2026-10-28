import { useEffect, useRef, useState } from "react"
import {
  type MetaFunction,
  isRouteErrorResponse,
  useRevalidator,
} from "react-router"

import { Loader2 } from "lucide-react"

import type { Route } from "./+types/detail"

import { CaptureNoteReadiness } from "@/components/notes/CaptureNoteReadiness"
import { NoteDetailRecovery } from "@/components/notes/NoteDetailRecovery"
import { SidebarInset } from "@/components/ui/sidebar"
import { LabResultsPanel } from "~/components/LabResultsPanel"
import { MedicationPanel } from "~/components/MedicationPanel"
import { PrivateVisitNote } from "~/components/PrivateVisitNote"
import { SessionPrepPriorNotes } from "~/components/SessionPrepPriorNotes"
import VisitNoteViewer from "~/components/VisitNoteViewer"
import VisitTranscriptViewer from "~/components/VisitTranscriptViewer"
import { Header } from "~/components/notes/Header"
import { EngagementBadge } from "~/components/shared/EngagementBadge"
import type { EngagementRoleKey } from "~/components/shared/EngagementBadge"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { StatusBadge } from "~/components/shared/StatusBadge"
import { type EngagementRole, noteDetail } from "~/configuration/client"
import { SETTINGS_STALE_TIME } from "~/configuration/tanstack-query"
import { toast } from "~/hooks/use-toast"
import { NOTE_STATUS_LABELS } from "~/lib/appointment"
import { appointmentDateFormat } from "~/lib/date"
import { loadNoteDetail } from "~/lib/note-detail-load"
import { withReadDeadline } from "~/lib/read-deadline"
import {
  NOTE_PROCESSING_TIMEOUT_MS,
  isProcessing,
  markReady,
  stopTracking,
  useRecentUploads,
} from "~/lib/recent-uploads"
import {
  type SessionPrepUiState,
  SovereignPanelHost,
  resolveSovereignProviderConfig,
} from "~/sovereign/SovereignPanelHost"
import { isEngagementEnabled } from "~/utils/engagement-feature"
import {
  isReferenceLabsEnabled,
  isReferenceToolkitEnabled,
} from "~/utils/reference-toolkit-feature"

import { useUser } from "@clerk/clerk-react"
import { useQuery } from "@tanstack/react-query"
import { settingsDataOptions } from "client/@tanstack/react-query.gen"

type Status = Parameters<typeof StatusBadge>[0]["status"]

export function EmptyNotes({
  captureRequired = false,
}: {
  captureRequired?: boolean
}) {
  return (
    <div className="mb-4">
      <h2 className="mb-2 text-xl font-semibold">Notes</h2>
      <div className="text-muted-foreground text-sm italic">
        {captureRequired
          ? "This visit was started in Scribe. Reopen it in Scribe to resume the recording and prepare its note. A recording uploaded separately cannot generate a note for this visit. Use a new visit for a separate upload."
          : "No note for this visit yet."}
      </div>
    </div>
  )
}

export const NOTE_PROCESSING_POLL_MS = 15_000

// A recent durable upload is visible across browsers. While the page is open
// and visible it checks only this visit, then revalidates when the note arrives
// or the server moves it to the honest "taking longer" state. Browser-local
// tracking remains a compatibility bridge for an older receiver response.
export function NoteProcessing({
  appointmentId,
  phase,
}: {
  appointmentId: string
  phase: "in-progress" | "taking-longer"
}) {
  const revalidator = useRevalidator()
  const revalidate = useRef(revalidator.revalidate)
  revalidate.current = revalidator.revalidate
  const uploads = useRecentUploads()
  const startedAt = uploads.startedAt.get(appointmentId)

  useEffect(() => {
    if (phase !== "in-progress") return
    let checking = false
    let active = true
    let pending: AbortController | undefined
    const poll = setInterval(async () => {
      if (checking || document.hidden) return
      checking = true
      const controller = new AbortController()
      pending = controller
      const timeout = setTimeout(
        () => controller.abort(),
        NOTE_PROCESSING_POLL_MS,
      )
      try {
        const { data } = await withReadDeadline(
          (signal) =>
            noteDetail({
              throwOnError: false,
              signal,
              path: { appointment_id: appointmentId },
            }),
          { signal: controller.signal },
        )
        if (
          active &&
          !controller.signal.aborted &&
          (data?.notes?.length ||
            (data?.note_status !== undefined &&
              data.note_status !== "in-progress"))
        )
          await revalidate.current()
      } catch {
        // SDK/auth/network failures may reject even with throwOnError false.
        // Keep the bounded processing state and let a later poll recover.
      } finally {
        clearTimeout(timeout)
        pending = undefined
        checking = false
      }
    }, NOTE_PROCESSING_POLL_MS)
    return () => {
      active = false
      clearInterval(poll)
      pending?.abort()
    }
  }, [appointmentId, phase])

  useEffect(() => {
    if (startedAt === undefined) return
    const remaining = startedAt + NOTE_PROCESSING_TIMEOUT_MS - Date.now()
    const giveUp = setTimeout(
      () => stopTracking(appointmentId),
      Math.max(remaining, 0),
    )
    return () => clearTimeout(giveUp)
  }, [appointmentId, startedAt])

  if (phase === "taking-longer") {
    return (
      <div role="status" className="mb-4">
        <div className="text-xl font-semibold">
          Your recording was received, but the note is taking longer than
          expected.
        </div>
        <p className="text-muted-foreground mt-1 text-sm">
          No note is ready yet. Check again when you are ready; this does not
          mean the upload failed.
        </p>
        <button
          type="button"
          className="mt-3 text-sm font-medium underline underline-offset-4"
          onClick={() => revalidator.revalidate()}
          disabled={revalidator.state !== "idle"}
        >
          {revalidator.state === "idle" ? "Check again" : "Checking…"}
        </button>
      </div>
    )
  }

  return (
    <div role="status" className="mb-4 flex items-start gap-3">
      <Loader2
        className="text-muted-foreground mt-1 size-5 motion-safe:animate-spin"
        aria-hidden="true"
      />
      <div>
        <div className="text-xl font-semibold">Writing your note…</div>
        <p className="text-muted-foreground text-sm">
          This usually takes a couple of minutes. You can stay here or come back
          later.
        </p>
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
  statusLabel,
  title,
  dataFields,
  engagementRole,
}: {
  status: Status
  statusLabel?: string
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
          <StatusBadge
            status={status}
            label={statusLabel ?? NOTE_STATUS_LABELS[status]}
          />
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
  const [sessionPrepOpenRequest, setSessionPrepOpenRequest] = useState(0)
  const [sessionPrepState, setSessionPrepState] =
    useState<SessionPrepUiState | null>(null)
  const sessionPrepBuildAvailable = resolveSovereignProviderConfig() !== null
  const { data: settings } = useQuery({
    ...settingsDataOptions(),
    staleTime: SETTINGS_STALE_TIME,
    enabled: sessionPrepBuildAvailable,
  })
  // The build-wide flag only makes the pinned provider integration available.
  // Core's current exact-account and access decision controls whether this
  // provider sees any Session Prep launcher or source-review affordance.
  const sessionPrepAvailable = Boolean(
    sessionPrepBuildAvailable &&
      settings?.session_prep?.eligible === true &&
      settings.session_prep.can_review_sources === true,
  )
  useEffect(() => {
    setSessionPrepState(null)
    setSessionPrepOpenRequest(0)
  }, [loaderData.appointment.id])
  const { transcript, notes, note_transcripts } = loaderData
  const hasNotes = notes && notes.length > 0
  const appointmentId = loaderData.appointment.id
  const uploads = useRecentUploads()
  const noteProgress = hasNotes
    ? null
    : isProcessing(uploads, appointmentId) ||
        loaderData.note_status === "in-progress"
      ? "in-progress"
      : loaderData.note_status === "taking-longer"
        ? "taking-longer"
        : null

  useEffect(() => {
    // markReady reports whether this visit was being tracked, so the toast
    // fires once even if the effect re-runs.
    if (hasNotes && markReady(appointmentId)) {
      toast({ title: "Your note is ready" })
    }
  }, [hasNotes, appointmentId])
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
          status={
            hasNotes
              ? "completed"
              : noteProgress === "in-progress"
                ? "in-progress"
                : "not-started"
          }
          statusLabel={
            noteProgress === "taking-longer" ? "Taking longer" : undefined
          }
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

        {loaderData.patient_identity_state === "provisional" && (
          <p className="text-muted-foreground mb-4 text-sm">
            Patient details can be matched later in Scribe. Your recording and
            note stay with this visit.
          </p>
        )}

        {sessionPrepAvailable &&
          loaderData.session_prep_prior_notes &&
          !loaderData.focusedNoteId && (
            <SessionPrepPriorNotes
              overview={loaderData.session_prep_prior_notes}
              preparationState={sessionPrepState}
              onOpenSessionPrep={() =>
                setSessionPrepOpenRequest((request) => request + 1)
              }
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
        ) : noteProgress ? (
          <NoteProcessing appointmentId={appointmentId} phase={noteProgress} />
        ) : loaderData.capture_workflow_required && transcript ? (
          <CaptureNoteReadiness key={loaderData.appointment.id} />
        ) : (
          <EmptyNotes captureRequired={loaderData.capture_workflow_required} />
        )}

        {referenceToolkitEnabled && (
          <MedicationPanel
            patientId={loaderData.patient.id}
            appointmentId={loaderData.appointment.id}
          />
        )}
        {loaderData.private_note_available && (
          <PrivateVisitNote key={appointmentId} appointmentId={appointmentId} />
        )}
        {referenceLabsEnabled && (
          <LabResultsPanel patientId={loaderData.patient.id} />
        )}

        {!distinctNoteSources && (
          <VisitTranscriptViewer transcript={sharedTranscript} />
        )}
      </InnerScrollableContent>
      {sessionPrepAvailable && (
        <SovereignPanelHost
          key={loaderData.appointment.id}
          appointmentId={loaderData.appointment.id}
          patientName={loaderData.patient.name}
          appointmentDate={loaderData.appointment.appointment_date}
          priorNotes={loaderData.session_prep_prior_notes}
          openRequest={sessionPrepOpenRequest}
          onSessionPrepStateChange={setSessionPrepState}
        />
      )}
    </SidebarInset>
  )
}
