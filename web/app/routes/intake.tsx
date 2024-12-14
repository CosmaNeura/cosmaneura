import { useState } from "react"
import { MetaFunction, useNavigation } from "react-router"
import { useOutletContext } from "react-router"

import { Send } from "lucide-react"

import type { Route } from "./+types.home"

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
import { mockData } from "@/data/mock"

// export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
//   const clerkClient = await getClient()
//   invariant(clerkClient, "clerk client should always exist or redirect")

//   // this route is authenticated
//   const { data } = await applicationData()

//   // TODO need idiomatic error handling here
//   if (data === undefined) {
//     throw new Error("Failed to load data")
//   }

//   return data
// }

export const meta: MetaFunction = () => {
  return [
    { title: "Intake" },
    {
      name: "description",
      content: "Welcome to Starter Application Template",
    },
  ]
}

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  return mockData
}

type ContextType = { onNavigate: (page: string) => void; currentPage: string }

export default function IntakePage({ loaderData }: Route.ComponentProps) {
  const { onNavigate, currentPage } = useOutletContext<ContextType>()
  const [selectedMail, setSelectedMail] = useState(null)
  const [isAccuityIntakeOpen, setIsAccuityIntakeOpen] = useState(false)
  const [isIntakeTranscriptOpen, setIsIntakeTranscriptOpen] = useState(false)

  const navigation = useNavigation()
  const isLoading = navigation.state === "loading"

  const resendIntake = () => {
    // Implement resend logic here
    console.log("Resending intake...")
  }

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
          isLoading={isLoading}
          loadingContent={<SkeletonMessageList />}
          mails={loaderData && loaderData.mails}
        />
        <SidebarInset>
          <header className="sticky top-0 flex shrink-0 items-center gap-2 border-b bg-background p-4">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-2 h-4" />
            <Breadcrumb>
              <h1 className="text-lg font-semibold">
                {selectedMail ? selectedMail.name : "Select a patient"}
              </h1>
            </Breadcrumb>
            <div className="ml-auto flex gap-2">
              {selectedMail && (
                <>
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
                        Send an email or text to the patient asking them to fill
                        out the intake form again
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </>
              )}
            </div>
          </header>
          <div className="flex flex-col gap-4 p-4">
            {isLoading ? (
              <SkeletonMessage />
            ) : selectedMail ? (
              <div className="transition-all duration-300 ease-in-out transform translate-y-0 opacity-100">
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
              <div className="flex items-center justify-center h-[calc(100vh-5rem)]">
                <div className="text-center text-muted-foreground">
                  <h2 className="text-xl font-semibold mb-2">
                    Select a patient
                  </h2>
                  <p className="text-sm">
                    Choose a patient from the list to view their details
                  </p>
                </div>
              </div>
            )}
          </div>
        </SidebarInset>
      </SidebarProvider>
    </PageTransition>
  )
}
