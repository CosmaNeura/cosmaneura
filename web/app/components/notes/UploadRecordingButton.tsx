import { ChangeEventHandler, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { CalendarIcon, CheckIcon, Upload } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
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
import {
  ComboBox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
} from "~/components/vendor/Combobox"
import { getClient } from "~/configuration/clerk"
import {
  generateSignedUploadUrl,
  uploadVisitAudioFile,
} from "~/configuration/client"
import { useToast } from "~/hooks/use-toast"
import { cn } from "~/lib/utils"

import { invariant } from "@epic-web/invariant"

function PatientComboboxSearch() {
  const PATIENTS = [
    { id: "patient_123", name: "John Doe", dob: "01/01/1990" },
    { id: "patient_456", name: "Jane Smith", dob: "02/02/1995" },
  ]

  const [value, setValue] = useState<string | null>(null)
  // const bookByValue = useMemo(
  //   () => (value && BOOKS.find((book) => book.id === value)) || null,
  //   [value],
  // )

  return (
    <>
      <ComboBox
        value={value}
        onValueChange={setValue}
        filterItems={(inputValue, items) =>
          items.filter(({ value }) => {
            const book = PATIENTS.find((book) => book.id === value)
            return (
              !inputValue ||
              (book &&
                book.name.toLowerCase().includes(inputValue.toLowerCase()))
            )
          })
        }
      >
        <ComboboxInput placeholder="Pick a book..." />
        <ComboboxContent>
          {PATIENTS.map(({ id, name, dob }) => (
            <ComboboxItem
              key={id}
              value={id}
              label={name}
              disabled={id === "book-5"}
              className="ps-8"
            >
              <span className="text-sm text-foreground">{name}</span>
              <span className="text-xs text-muted-foreground">{dob}</span>
              {value === id && (
                <span className="absolute start-2 top-0 flex h-full items-center justify-center">
                  <CheckIcon className="size-4" />
                </span>
              )}
            </ComboboxItem>
          ))}
          <ComboboxEmpty>No results.</ComboboxEmpty>
        </ComboboxContent>
      </ComboBox>
    </>
  )
}

function TestFileForm({ closeDialogBox }: { closeDialogBox: () => void }) {
  const { toast } = useToast()

  // TODO look up audio file types here": https://www.assemblyai.com/docs/getting-started/transcribe-an-audio-file

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
            <>
              <FormItem>
                <FormLabel>Appointment Date</FormLabel>
                <FormControl>
                  <Popover>
                    <PopoverTrigger asChild>
                      <Button
                        variant="outline"
                        className={cn(
                          "w-full justify-start text-left font-normal",
                          !field.value && "text-muted-foreground",
                        )}
                      >
                        <CalendarIcon className="mr-2 h-4 w-4" />
                        {field.value
                          ? format(field.value, "PPP")
                          : "Pick a date"}
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-auto p-0" align="start">
                      <Calendar
                        mode="single"
                        selected={field.value}
                        onSelect={field.onChange}
                        initialFocus
                      />
                    </PopoverContent>
                  </Popover>
                </FormControl>
                <FormMessage />
              </FormItem>

              <FormItem>
                <FormLabel>Patient Name</FormLabel>
                <FormControl>
                  <PatientComboboxSearch />
                  {/* <Input
                    placeholder="Enter patient name"
                    {...form.register("patientName")}
                  /> */}
                </FormControl>
                <FormMessage />
              </FormItem>

              <FormItem>
                <FormLabel>Date of Birth</FormLabel>
                <FormControl>
                  <Popover>
                    <PopoverTrigger asChild>
                      <Button
                        variant="outline"
                        className={cn(
                          "w-full justify-start text-left font-normal",
                          !field.value && "text-muted-foreground",
                        )}
                      >
                        <CalendarIcon className="mr-2 h-4 w-4" />
                        {field.value
                          ? format(field.value, "PPP")
                          : "Pick a date"}
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-auto p-0" align="start">
                      <Calendar
                        mode="single"
                        selected={field.value}
                        onSelect={field.onChange}
                        initialFocus
                      />
                    </PopoverContent>
                  </Popover>
                </FormControl>
                <FormMessage />
              </FormItem>

              <FormItem>
                <FormDescription>
                  Upload an audio recording from a patient visit.
                </FormDescription>
                <FormControl>
                  <Input type="file" {...field} onChange={handleFileChange} />
                </FormControl>
                <FormMessage />
              </FormItem>
            </>
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
