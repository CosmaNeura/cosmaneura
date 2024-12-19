import { useState } from "react"
import { useForm } from "react-hook-form"
import { useNavigation } from "react-router"
import { useOutletContext } from "react-router"
import { z } from "zod"

import type { Route } from "./+types.notes"

import { AppSidebar } from "@/components/app-sidebar"
import { UploadRecordingButton } from "@/components/notes/UploadRecordingButton"
import { PageTransition } from "@/components/page-transition"
import { MessageContent } from "@/components/shared/MessageContent"
import { SkeletonMessage } from "@/components/skeleton-message"
import { SkeletonMessageList } from "@/components/skeleton-message-list"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { mockData } from "@/data/mock"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "~/components/ui/form"
import { Input } from "~/components/ui/input"
import { getClient } from "~/configuration/clerk"
import { noteData, visitAudioFileGetSignedUrl } from "~/configuration/client"

import { invariant } from "@epic-web/invariant"

type ContextType = { onNavigate: (page: string) => void; currentPage: string }

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  const clerkClient = await getClient()
  invariant(clerkClient, "clerk client should always exist or redirect")

  // this route is authenticated
  const { data } = await noteData()

  // TODO need idiomatic error handling here
  if (data === undefined) {
    throw new Error("Failed to load data")
  }

  return mockData
}

function TestFileForm() {
  // const formSchema = z.object({
  //   image: z
  //     //Rest of validations done via react dropzone
  //     .instanceof(File)
  //     // .refine((file) => file.size !== 0, "Please upload an image"),
  // });

  // const form = useForm<z.infer<typeof formSchema>>({
  const form = useForm({
    // resolver: zodResolver(formSchema),
    // mode: "onBlur",
    // defaultValues: {
    // image: new File([""], "filename"),
    // },
  })

  // function onSubmit(values: z.infer<typeof formSchema>) {
  async function onSubmit(_values: z.infer<typeof formSchema>) {
    const { data: sasSignature } = await visitAudioFileGetSignedUrl()
    invariant(sasSignature, "sasSignature should always exist")

    const unixTimestamp = Date.now()
    const userClient = await getClient()
    invariant(userClient && userClient.user, "userClient should always exist")

    const stampedFileName =
      userClient.user.id + "_" + unixTimestamp + "_" + file.name

    const url = new URL(sasSignature)
    url.pathname = `${url.pathname}/${stampedFileName}`
    const finalUrl = url.toString()

    const _response = await fetch(finalUrl, {
      method: "PUT",
      headers: {
        "x-ms-blob-type": "BlockBlob",
        "Content-Type": file.type,
      },
      body: file,
    })
  }

  const [file, setFile] = useState<File | null>(null)

  const handleFileChange = (e) => {
    const selectedFile = e.target.files[0]
    setFile(selectedFile)
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <FormField
          control={form.control}
          name="image"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Username</FormLabel>
              <FormControl>
                <Input type="file" {...field} onChange={handleFileChange} />
              </FormControl>
              <FormDescription>
                This is your public display name.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit">Submit</Button>
      </form>
    </Form>
  )
}

export default function NotePage({ loaderData }: Route.ComponentProps) {
  const { onNavigate, currentPage } = useOutletContext<ContextType>()
  const [isSOAPOpen, setIsSOAPOpen] = useState(false)
  const [isNewPatientOpen, setIsNewPatientOpen] = useState(false)
  const [isTelehealthSOAPOpen, setIsTelehealthSOAPOpen] = useState(false)
  const [isTelehealthHPOpen, setIsTelehealthHPOpen] = useState(false)
  const [isTranscriptOpen, setIsTranscriptOpen] = useState(false)
  const [selectedMail, setSelectedMail] = useState(null)
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
          isLoading={isLoading}
          loadingContent={<SkeletonMessageList />}
          mails={loaderData.mails}
        />
        <SidebarInset>
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
          <div className="flex flex-col gap-4 p-4">
            {isLoading ? (
              <SkeletonMessage />
            ) : selectedMail ? (
              <div className="translate-y-0 transform opacity-100 transition-all duration-300 ease-in-out">
                <MessageContent
                  mail={selectedMail}
                  isSOAPOpen={isSOAPOpen}
                  setIsSOAPOpen={setIsSOAPOpen}
                  isNewPatientOpen={isNewPatientOpen}
                  setIsNewPatientOpen={setIsNewPatientOpen}
                  isTelehealthSOAPOpen={isTelehealthSOAPOpen}
                  setIsTelehealthSOAPOpen={setIsTelehealthSOAPOpen}
                  isTelehealthHPOpen={isTelehealthHPOpen}
                  setIsTelehealthHPOpen={setIsTelehealthHPOpen}
                  isTranscriptOpen={isTranscriptOpen}
                  setIsTranscriptOpen={setIsTranscriptOpen}
                />
              </div>
            ) : (
              <div className="flex h-[calc(100vh-5rem)] items-center justify-center">
                <div className="text-center text-muted-foreground">
                  <h2 className="mb-2 text-xl font-semibold">
                    Select a patient
                  </h2>
                  <p className="text-sm">
                    Choose a patient from the list to view their details
                  </p>
                  <TestFileForm />
                </div>
              </div>
            )}
          </div>
        </SidebarInset>
      </SidebarProvider>
    </PageTransition>
  )
}
