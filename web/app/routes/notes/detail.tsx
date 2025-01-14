import { useState } from "react"
import type { MetaFunction } from "react-router"
import { useOutletContext } from "react-router"

import { Route } from "./+types/detail"

import { AppSidebar } from "@/components/app-sidebar"
import { UploadRecordingButton } from "@/components/notes/UploadRecordingButton"
import { PageTransition } from "@/components/page-transition"
import { EmptyNotes, MessageContent } from "@/components/shared/MessageContent"
import { SkeletonMessage } from "@/components/skeleton-message"
import { SkeletonMessageList } from "@/components/skeleton-message-list"
import { Separator } from "@/components/ui/separator"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import VisitNoteViewer from "~/components/VisitNoteViewer"
import VisitTranscriptViewer from "~/components/VisitTranscriptViewer"
import { getClient } from "~/configuration/clerk"
import { noteDetail, noteList } from "~/configuration/client"

import { invariant } from "@epic-web/invariant"
import VisitNoteViewer from "~/components/VisitNoteViewer"
import VisitTranscriptViewer from "~/components/VisitTranscriptViewer"
import { FeedbackWidget } from "~/components/shared/FeedbackWidget"
import { StatusBadge } from "~/components/shared/StatusBadge"

export async function clientLoader(loaderArgs: Route.ClientLoaderArgs) {
  const clerkClient = await getClient()
  invariant(clerkClient, "clerk client should always exist or redirect")

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

interface Mail {
  name: string
  // Add other mail properties as needed
}

function Header({ selectedMail }: { selectedMail: Mail | null }) {
  return (
    <header className="sticky top-0 flex shrink-0 items-center gap-2 border-b bg-background p-4">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <h1 className="text-lg font-semibold">
        {selectedMail ? selectedMail.name : "Select a patient"}
      </h1>
      <div className="ml-auto">
        <UploadRecordingButton />
      </div>
    </header>
  )
}

function Content({
  isLoading = false,
  children,
}: {
  isLoading?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-4 p-4">
      {isLoading ? (
        <SkeletonMessage />
      ) : (
          <div className="translate-y-0 transform opacity-100 transition-all duration-300 ease-in-out">
                <div className="space-y-6">
      {/* TODO pretty sure I should remove all of this transition stuff... */}
      <div className={"transform transition-all duration-150 ease-in-out"}>
        <div className="rounded-lg border bg-card p-6 shadow-sm">
{children}
        </div>
      </div>

      <div className="flex flex-col gap-4 sm:flex-row">
        <FeedbackWidget />
      </div>
    </div>

        </div>
      )}
    </div>
  )
}

function NoteHeader({
  status
}: {
  status: Status
}) {
  return (
    <div className="mb-6 flex items-start justify-between">
      <div className="text-left">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">John Doe</h1>
          <StatusBadge status={status} />
        </div>
        <div className="mt-1 space-y-1">
          <p className="text-sm">
            <span className="font-medium text-muted-foreground">
              Appointment Date:
            </span>{" "}
            <span>fake-date</span>
          </p>
            <p className="text-sm">
              <span className="font-medium text-muted-foreground">
                Intake Completed At:
              </span>{" "}
              <span>Completed at</span>
            </p>
          <p className="text-sm">
            <span className="font-medium text-muted-foreground">Location:</span>{" "}
            <span>Denver, CO</span>
          </p>
        </div>
      </div>
    </div>
  )
}

export default function NoteDetailPage({ loaderData }: Route.ComponentProps) {
  const { transcript, notes } = loaderData
  const hasNotes = notes.length > 0

  return (
    <SidebarInset>
      <Header />
      <Content>
        <NoteHeader status={hasNotes ? "completed" : "not-started"} />

        {notes.length ? (
          notes.map((note, index) => (
            <VisitNoteViewer note={note} defaultOpen={index === 0} />
          ))
        ) : (
          <EmptyNotes />
        )}

        <VisitTranscriptViewer transcript={transcript} />

        {/* <PatientDataSections className="mb-6" mail={currentMail} /> */}

        {/* {!showTemplates && (
            <MessageIntake
              mail={currentMail}
              isAccuityIntakeOpen={isAccuityIntakeOpen}
              setIsAccuityIntakeOpen={setIsAccuityIntakeOpen}
              isIntakeTranscriptOpen={isIntakeTranscriptOpen}
              setIsIntakeTranscriptOpen={setIsIntakeTranscriptOpen}
            />
          )} */}
      </Content>
    </SidebarInset>
  )
}
