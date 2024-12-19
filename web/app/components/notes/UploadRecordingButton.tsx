import { ChangeEventHandler, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Upload } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { Icons } from "~/components/icons"
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
import {
  generateSignedUploadUrl,
  uploadVisitAudioFile,
} from "~/configuration/client"
import { useToast } from "~/hooks/use-toast"

import { invariant } from "@epic-web/invariant"

type ContextType = { onNavigate: (page: string) => void; currentPage: string }

function TestFileForm({ closeDialogBox }: { closeDialogBox: () => void }) {
  const { toast } = useToast()

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
    const { data: sasSignature } = await generateSignedUploadUrl()
    invariant(sasSignature, "sasSignature should always exist")

    const unixTimestamp = Date.now()
    const userClient = await getClient()
    invariant(userClient && userClient.user, "userClient should always exist")

    const stampedFileName =
      userClient.user.id + "_" + unixTimestamp + "_" + file.name

    const url = new URL(sasSignature)
    url.pathname = `${url.pathname}/${stampedFileName}`
    const finalUrl = url.toString()

    const _azureUploadResponse = await fetch(finalUrl, {
      method: "PUT",
      headers: {
        "x-ms-blob-type": "BlockBlob",
        "Content-Type": file.type,
      },
      body: file,
    })

    const _internalResponse = await uploadVisitAudioFile({
      query: {
        file_name: stampedFileName,
      },
    })

    toast({
      title: "Upload complete",
      description:
        "Your audio recording has been uploaded successfully. Check back in a couple minutes.",
    })

    closeDialogBox()
  }

  const [file, setFile] = useState<File | null>(null)

  const handleFileChange = (e: ChangeEventHandler<HTMLInputElement>) => {
    const selectedFile = e.target.files[0]
    setFile(selectedFile)
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <FormField
          control={form.control}
          name="file_upload"
          render={({ field }) => (
            <FormItem>
              <FormDescription>
                Upload an audio recording from a patient visit.
              </FormDescription>
              <FormControl>
                <Input type="file" {...field} onChange={handleFileChange} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit" disabled={form.formState.isSubmitting || !file}>
          {form.formState.isSubmitting ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Uploading...
            </>
          ) : (
            "Upload"
          )}
        </Button>
      </form>
    </Form>
  )
}
export function UploadRecordingButton() {
  const [open, setOpen] = useState(false)

  const closeDialogBox = () => {
    setOpen(false)
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <Dialog open={open} onOpenChange={setOpen}>
          <TooltipTrigger asChild>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm" className="hidden md:flex">
                <Upload className="mr-2 h-4 w-4" />
                Upload Recording
              </Button>
            </DialogTrigger>
          </TooltipTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Upload Recording</DialogTitle>
            </DialogHeader>
            <TestFileForm closeDialogBox={closeDialogBox} />
          </DialogContent>
        </Dialog>
        <TooltipContent>
          Upload an audio recording of the patient visit
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
