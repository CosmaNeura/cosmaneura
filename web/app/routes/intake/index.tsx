import { intakeList } from "client"
import { useState } from "react"
import type { MetaFunction } from "react-router"
import { useOutletContext } from "react-router"

import { Send } from "lucide-react"

import type { Route } from "../+types/intake"

import { AppSidebar } from "@/components/app-sidebar"
import { PageTransition } from "@/components/page-transition"
import { MessageContent } from "@/components/shared/MessageContent"
import { SkeletonMessage } from "@/components/skeleton-message"
import { SkeletonMessageList } from "@/components/skeleton-message-list"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getClient } from "~/configuration/clerk"

import { invariant } from "@epic-web/invariant"

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  // const clerkClient = await getClient()
  // invariant(clerkClient, "clerk client should always exist or redirect")

  // // this route is authenticated
  // const { data } = await intakeList()

  // // TODO need idiomatic error handling here
  // if (data === undefined) {
  //   throw new Error("Failed to load data")
  // }

  return {}
}

export const meta: MetaFunction = () => {
  return [
    { title: "Intake" },
    {
      name: "description",
      content: "Your patient intake data",
    },
  ]
}

type ContextType = { onNavigate: (page: string) => void; currentPage: string }

function ResendIntakeButton() {
  function resendIntake() {
    // TODO: implement resend intake
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className="hidden md:flex"
            onClick={resendIntake}
          >
            <Send className="mr-2 h-4 w-4" />
            Resend Intake
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          Send an email or text to the patient asking them to fill out the
          intake form again
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

const Header = ({ selectedMail }: { selectedMail: Route.Mail | null }) => (
  <header className="sticky top-0 flex shrink-0 items-center gap-2 border-b bg-background p-4">
    <SidebarTrigger className="-ml-1" />
    <Separator orientation="vertical" className="mr-2 h-4" />
    <Breadcrumb>
      <h1 className="text-lg font-semibold">
        {selectedMail ? selectedMail.name : "Select a patient"}
      </h1>
    </Breadcrumb>
    <div className="ml-auto flex gap-2">
      {selectedMail && <ResendIntakeButton />}
    </div>
  </header>
)
type ContentProps = {
  isLoading: boolean
  selectedMail: Route.Mail | null
  isAccuityIntakeOpen: boolean
  setIsAccuityIntakeOpen: (value: boolean) => void
  isIntakeTranscriptOpen: boolean
  setIsIntakeTranscriptOpen: (value: boolean) => void
}

const BlankMessage = () => (
  <div className="flex h-[calc(100vh-5rem)] items-center justify-center">
    <div className="text-center text-muted-foreground">
      <h2 className="mb-2 text-xl font-semibold">Select a patient</h2>
      <p className="text-sm">
        Choose a patient from the list to view their details
      </p>
    </div>
  </div>
)

const Content = ({
  isLoading,
  selectedMail,
  isAccuityIntakeOpen,
  setIsAccuityIntakeOpen,
  isIntakeTranscriptOpen,
  setIsIntakeTranscriptOpen,
}: ContentProps) => (
  <div className="flex flex-col gap-4 p-4">
    {isLoading ? (
      <SkeletonMessage />
    ) : selectedMail ? (
      <div className="translate-y-0 transform opacity-100 transition-all duration-300 ease-in-out">
        <MessageContent
          mail={{
            ...selectedMail,
            status: "completed",
            appointmentDate: selectedMail.date,
            intakeCompletedAt: "March 13, 2024 2:30 PM",
          }}
          showTemplates={false}
          isAccuityIntakeOpen={isAccuityIntakeOpen}
          setIsAccuityIntakeOpen={setIsAccuityIntakeOpen}
          isIntakeTranscriptOpen={isIntakeTranscriptOpen}
          setIsIntakeTranscriptOpen={setIsIntakeTranscriptOpen}
          showStatus
        />
      </div>
    ) : (
      <BlankMessage />
    )}
  </div>
)

export default function IntakeIndex({ loaderData }: Route.ComponentProps) {
  const { onNavigate, currentPage } = useOutletContext<ContextType>()
  const [selectedMail, setSelectedMail] = useState<Route.Mail | null>(null)
  const [isAccuityIntakeOpen, setIsAccuityIntakeOpen] = useState(false)
  const [isIntakeTranscriptOpen, setIsIntakeTranscriptOpen] = useState(false)

  const navigation = useNavigation()
  const isLoading = navigation.state === "loading"
  return <>hi</>

  return (
    <SidebarInset>
      <Header selectedMail={selectedMail} />
      <Content
        isLoading={isLoading}
        selectedMail={selectedMail}
        isAccuityIntakeOpen={isAccuityIntakeOpen}
        setIsAccuityIntakeOpen={setIsAccuityIntakeOpen}
        isIntakeTranscriptOpen={isIntakeTranscriptOpen}
        setIsIntakeTranscriptOpen={setIsIntakeTranscriptOpen}
      />
    </SidebarInset>
  )
}
