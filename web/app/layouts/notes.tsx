import { Outlet, data, href, isRouteErrorResponse } from "react-router"

import type { Route } from "./+types/notes"

import { AppSidebar, type SidebarEntry } from "@/components/app-sidebar"
import { NotesKeyboardShortcuts } from "@/components/notes/KeyboardShortcuts"
import { NoteDetailRecovery } from "@/components/notes/NoteDetailRecovery"
import { PracticeAgreementWall } from "@/components/notes/PracticeAgreementWall"
import { UploadRecordingButton } from "@/components/notes/UploadRecordingButton"
import { SidebarProvider } from "@/components/ui/sidebar"
import { queryClient } from "~/configuration/tanstack-query"
import {
  NOTE_STATUS_LABELS,
  appointmentsToSidebarEntries,
} from "~/lib/appointment"
import { type NotesLayoutData, loadNotesLayoutData } from "~/lib/notes-list"
import { practiceAgreementBlockFromRouteFailure } from "~/lib/practice-gate-error"
import { ReadDeadlineError, withReadDeadline } from "~/lib/read-deadline"
import { isProcessing, useRecentUploads } from "~/lib/recent-uploads"
import { useTerminology } from "~/utils/terminology"

import { noteListOptions } from "client/@tanstack/react-query.gen"

export { loadNotesLayoutData, type NotesLayoutData }

// Successful lists retain the generated query's cache shape for notes consumers.
export async function clientLoader({ request }: Route.ClientLoaderArgs) {
  try {
    return await withReadDeadline(() => loadNotesLayoutData(), {
      signal: request.signal,
      onTimeout: () => {
        void queryClient.cancelQueries({
          queryKey: noteListOptions().queryKey,
          exact: true,
        })
      },
    })
  } catch (error) {
    if (error instanceof ReadDeadlineError) {
      throw data({ notesLoadFailure: "temporary" }, { status: 503 })
    }
    throw error
  }
}

export function ErrorBoundary({ error }: { error: unknown }) {
  const blocked = practiceAgreementBlockFromRouteFailure(error)
  if (blocked) return <PracticeAgreementWall block={blocked} />
  if (
    isRouteErrorResponse(error) &&
    error.status === 503 &&
    error.data?.notesLoadFailure === "temporary"
  ) {
    return (
      <SidebarProvider>
        <NoteDetailRecovery kind="temporary" visits />
      </SidebarProvider>
    )
  }
  throw error
}

// A visit uploaded in this session reads "Writing note" until its note
// arrives, then is highlighted for a moment.
export function withUploadProgress(
  entry: SidebarEntry,
  uploads: ReturnType<typeof useRecentUploads>,
): SidebarEntry {
  if (isProcessing(uploads, entry.id) && entry.status !== "completed") {
    return {
      ...entry,
      status: "in-progress",
      statusLabel: NOTE_STATUS_LABELS["in-progress"],
    }
  }
  return uploads.justReady.has(entry.id) ? { ...entry, highlight: true } : entry
}

export default function NotesLayout({
  loaderData,
}: {
  loaderData: NotesLayoutData
}) {
  const t = useTerminology()
  const recentUploads = useRecentUploads()

  if (loaderData.blocked) {
    return <PracticeAgreementWall block={loaderData.blocked} />
  }

  const { appointments, noteStatuses } = loaderData

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
        searchPlaceholder={`Search ${t.patientPlural} or visits…`}
        headerAction={<NotesKeyboardShortcuts />}
        listShortcuts
        emptyState={{
          title: "No visits yet",
          description: "Upload a visit recording to create your first note.",
          action: <UploadRecordingButton />,
        }}
        entries={appointmentsToSidebarEntries(
          appointments,
          (id: string) => href("/notes/:id", { id }),
          noteStatuses,
        ).map((entry) => withUploadProgress(entry, recentUploads))}
      />
      <Outlet />
    </SidebarProvider>
  )
}
