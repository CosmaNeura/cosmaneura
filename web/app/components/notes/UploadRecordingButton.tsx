import { props } from "ramda"
import { ChangeEventHandler, useState } from "react"
import { ControllerRenderProps, FieldValues, useForm } from "react-hook-form"
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
import { getClient, getUserId } from "~/configuration/clerk"
import {
  Patient,
  client,
  generateSignedVisitRecordingUploadUrl,
  uploadVisitAudioFile,
} from "~/configuration/client"
import { useToast } from "~/hooks/use-toast"
import { standardDateFormat } from "~/lib/date"
import { cn } from "~/lib/utils"

import { SimpleComboBox } from "../shared/SimpleComboBox"
import { invariant } from "@epic-web/invariant"
import { QueryClientProvider, useQuery } from "@tanstack/react-query"
import { patientListOptions } from "client/@tanstack/react-query.gen"

function PatientComboboxSearch({
  // onSelectedItemChange,
  form,
  onChange,
  value,
  ...props
}: {
  form: ReturnType<typeof useForm>
  // onSelectedItemChange: React.ComponentProps<
  //   typeof ComboBox
  // >["onSelectedItemChange"]
  onChange: ControllerRenderProps<FieldValues, string>["onChange"]
  value: ControllerRenderProps<FieldValues, string>["value"]
}) {
  const { data, error } = useQuery({ ...patientListOptions({ client }) })

  if (!data) {
    return <div>Loading...</div>
  }

  function transformPatientForComboBox(patient: Patient): {
    value: string
    label: string
  } {
    return {
      value: patient.id,
      label: patient.name,
    }
  }

  function handleChange(value: string) {
    const patient = data.find((patient) => patient.id === value)

    onChange(patient.name)
    form.setValue("date_of_birth", patient.date_of_birth)
  }

  return (
    <SimpleComboBox
      onChange={handleChange}
      value={value}
      items={data.map(transformPatientForComboBox)}
      // onSelectedItemChange={onSelectedItemChange}
      // renderItem={(item) => (
      //   <>
      //     <span className="text-sm text-foreground">{name}</span>
      //     <span className="text-xs text-muted-foreground">{item.dob}</span>
      //     {value === id && (
      //       <span className="absolute start-2 top-0 flex h-full items-center justify-center">
      //         <CheckIcon className="size-4" />
      //       </span>
      //     )}
      //   </>
      // )}
      {...props}
    />
  )

  // const PATIENTS = [
  //   { id: "patient_123", name: "John Doe", dob: "01/01/1990" },
  //   { id: "patient_456", name: "Jane Smith", dob: "02/02/1995" },
  // ]

  // const [value, setValue] = useState<string | null>(null)
  // // const bookByValue = useMemo(
  // //   () => (value && BOOKS.find((book) => book.id === value)) || null,
  // //   [value],
  // // )

  // return (
  //   <ComboBox
  //     // TODO should figure out how to extract the default value from the field
  //     onSelectedItemChange={onSelectedItemChange}
  //     filterItems={(inputValue, items) =>
  //       items.filter(({ value }) => {
  //         const book = PATIENTS.find((book) => book.id === value)
  //         return (
  //           !inputValue ||
  //           (book && book.name.toLowerCase().includes(inputValue.toLowerCase()))
  //         )
  //       })
  //     }
  //   >
  //     <ComboboxInput placeholder="Pick a book..." />
  //     <ComboboxContent>
  //       {PATIENTS.map(({ id, name, dob }) => (
  //         <ComboboxItem
  //           key={id}
  //           value={id}
  //           label={name}
  //           disabled={id === "book-5"}
  //           className="ps-8"
  //         >

  //         </ComboboxItem>
  //       ))}
  //       <ComboboxEmpty>No results.</ComboboxEmpty>
  //     </ComboboxContent>
  //   </ComboBox>
  // )
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

  // this took an embarrasing amount of time to figure out
  async function onSubmit(_values: z.infer<typeof formSchema>) {
    console.log(_values)
    return
    const { data: sasSignature } = await generateSignedVisitRecordingUploadUrl()
    invariant(sasSignature, "sasSignature should always exist")

    invariant(file, "file should be set at this stage ")

    const unixTimestamp = Date.now()
    const userId = await getUserId()
    const stampedFileName = [userId, unixTimestamp, file.name].join("_")
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
          name="appointment_date"
          defaultValue={new Date()}
          render={({ field }) => (
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
                        ? standardDateFormat(field.value)
                        : "Pick a date"}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <Calendar
                      mode="single"
                      selected={field.value}
                      onSelect={field.onChange}
                      defaultMonth={new Date()}
                      disabled={(date) =>
                        // two months ago, this is an arbitrary date
                        date <
                          new Date(Date.now() - 60 * 24 * 60 * 60 * 1000) ||
                        date > new Date()
                      }
                    />
                  </PopoverContent>
                </Popover>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="patient_name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Patient Name</FormLabel>
              <FormControl>
                <PatientComboboxSearch {...field} form={form} />
              </FormControl>
              <FormDescription>
                Search for an existing patient, or just type in a new patients
                name to create a new patient.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="date_of_birth"
          render={({ field }) => (
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
                        ? standardDateFormat(field.value)
                        : "Pick a date"}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <Calendar
                      mode="single"
                      selected={field.value}
                      onSelect={field.onChange}
                    />
                  </PopoverContent>
                </Popover>
              </FormControl>
              <FormDescription>
                Required to identify the patient in the future
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="audio_file"
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
