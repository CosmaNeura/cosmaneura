import { useState } from "react"
import { MetaFunction, useNavigation } from "react-router"
import { useOutletContext } from "react-router"

import type { Route } from "./+types/notes"

import { AppSidebar } from "@/components/app-sidebar"
import { UploadRecordingButton } from "@/components/notes/UploadRecordingButton"
import { PageTransition } from "@/components/page-transition"
import { MessageContent } from "@/components/shared/MessageContent"
import { SkeletonMessage } from "@/components/skeleton-message"
import { SkeletonMessageList } from "@/components/skeleton-message-list"
import { Separator } from "@/components/ui/separator"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { getClient } from "~/configuration/clerk"
import { noteList } from "~/configuration/client"

import { invariant } from "@epic-web/invariant"
import type { Route } from "./+types/intake"

type ContextType = { onNavigate: (page: string) => void; currentPage: string }

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  const clerkClient = await getClient()
  invariant(clerkClient, "clerk client should always exist or redirect")

  // this route is authenticated
  const { data } = await noteList()

  // TODO need idiomatic error handling here
  if (data === undefined) {
    throw new Error("Failed to load data")
  }

  return data
}

export const meta: MetaFunction = () => {
  return [
    { title: "Notetaking" },
    {
      name: "description",
      content: "Your patient meeting notes",
    },
  ]
}

export function EmptySelectionState() {
  return (
    <div className="flex h-[calc(100vh-5rem)] items-center justify-center">
      <div className="text-center text-muted-foreground">
        <h2 className="mb-2 text-xl font-semibold">Select a patient</h2>
        <p className="text-sm">
          Choose a patient from the list to view their details
        </p>
      </div>
    </div>
  )
}

interface Mail {
  name: string
  // Add other mail properties as needed
}

interface HeaderProps {
  selectedMail: Mail | null
}

function Header({ selectedMail }: HeaderProps) {
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

interface ContentProps {
  isLoading: boolean
  selectedMail: Mail | null
  appointmentNotes: object[]
}

function Content({ isLoading, selectedMail, appointmentNotes }: ContentProps) {
  return (
    <div className="flex flex-col gap-4 p-4">
      {isLoading ? (
        <SkeletonMessage />
      ) : selectedMail ? (
        <div className="translate-y-0 transform opacity-100 transition-all duration-300 ease-in-out">
          <MessageContent mail={selectedMail} notes={appointmentNotes} />
        </div>
      ) : (
        <EmptySelectionState />
      )}
    </div>
  )
}

export default function NotePage({ loaderData }: Route.ComponentProps) {
  const { onNavigate, currentPage } = useOutletContext<ContextType>()
  const appointmentNotes: object[] = []

  const [selectedMail, setSelectedMail] = useState<Mail | null>(null)
  const navigation = useNavigation()
  const isLoading = navigation.state === "loading"

  return (
    <PageTransition>
      <SidebarProvider
        style={
          {
            "--sidebar-width": "350px",
          } as React.CSSProperties
        }
      >
        <AppSidebar
          onMailSelect={setSelectedMail}
          selectedMail={selectedMail}
          onNavigate={onNavigate}
          currentPage={currentPage}
          isLoading={!loaderData}
          loadingContent={<SkeletonMessageList />}
          mails={loaderData && loaderData.appointments}
        />
        <SidebarInset>
          <Header selectedMail={selectedMail} />
          <Content
            isLoading={isLoading}
            selectedMail={selectedMail}
            appointmentNotes={appointmentNotes}
          />
        </SidebarInset>
      </SidebarProvider>
    </PageTransition>
  )
}
