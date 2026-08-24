import { addDays, subMonths } from "date-fns"
import { ChangeEventHandler, useState } from "react"
import { ControllerRenderProps, UseFormReturn, useForm } from "react-hook-form"
import { z } from "zod"

import { CheckIcon, Upload } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
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
import { DateTimePicker } from "~/components/lingua-time/datetime-picker"
import { AudioVideoDropzone } from "~/components/shared/AudioVideoDropzone"
import { SimpleComboBox } from "~/components/shared/SimpleComboBox"
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
import { ComboboxItemBase } from "~/components/vendor/Combobox"
import { Patient, client } from "~/configuration/client"
import { useToast } from "~/hooks/use-toast"
import { standardDateFormat } from "~/lib/date"
import { submitVisitAudioUpload } from "~/lib/visit-audio-upload"
import { useTerminology } from "~/utils/terminology"

import * as Sentry from "@sentry/react"
import { invariant } from "@epic-web/invariant"
import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
import { patientListOptions } from "client/@tanstack/react-query.gen"

type UploadRecordingFormValues = {
  appointment_date: Date
  patient_name: string
  date_of_birth: Date
  file: File
}

// TODO should really be extracted out to a shared component, but it's too married into this page at this point... :/
function PatientComboboxSearch({
  // required for updating the DOB when a patient item is selected
  form,
  onChange,
  value,
  ...props
}: {
  form: UseFormReturn<UploadRecordingFormValues>
  onChange: ControllerRenderProps<
    UploadRecordingFormValues,
    "patient_name"
  >["onChange"]
  value: ControllerRenderProps<
    UploadRecordingFormValues,
    "patient_name"
  >["value"]
}) {
  const { data, error } = useQuery({ ...patientListOptions({ client }) })
  const patients = data ?? []

  if (!data) {
    return <div>Loading...</div>
  }

  // TODO I need to better document this entire mess and upstream some of the combobox improvements

  function transformPatientForComboBox(
    patient: Patient,
  ): ComboboxItemBase & Patient {
    return {
      ...patient,
      value: patient.id,
      label: patient.name,
    }
  }

  // TODO need to fix the typing in the underlying simple combox wrapper
  function onSelectedItemChange(item: ComboboxItemBase | null) {
    if (!item) {
      return
    }

    // `new_item` is a special key to used to indicate that a value was typed in, and it wasn't selected from the list
    // when this occurs, we want to set the field value to the inputted text (label)
    if (item.value == "new_item") {
      onChange(item.label)
    } else {
      const selected_patient_id = item.value
      const patient = patients.find(
        (patient) => patient.id === selected_patient_id,
      )
      invariant(patient, "patient should exist if selected from combobox")

      // update the patient name field with the name of the patient, not the ID
      onChange(patient.name)

      form.setValue("date_of_birth", new Date(patient.date_of_birth))
    }
  }

  return (
    <SimpleComboBox
      // onChange={handleChange}
      onSelectedItemChange={onSelectedItemChange}
      value={value}
      items={data.map(transformPatientForComboBox)}
      // TODO get DOB added to the item rendering
      // TODO(mbianco) renderItem only gets the value + label of the field, we should really pass
      //               a full object so we can do more advanced rendering...
      renderItem={(item) =>
        (() => {
          const patientItem = item as ComboboxItemBase &
            Patient & { isSelected?: boolean }

          return (
            <>
              <span className="text-foreground text-sm">
                {patientItem.label}
              </span>
              <span className="text-muted-foreground text-xs">
                {standardDateFormat(patientItem.date_of_birth)}
              </span>
              {patientItem.isSelected && (
                <span className="absolute start-2 top-0 flex h-full items-center justify-center">
                  <CheckIcon className="size-4" />
                </span>
              )}
            </>
          )
        })()
      }
      {...props}
    />
  )
}

function VisitAudioUploadForm({
  closeDialogBox,
}: {
  closeDialogBox: () => void
}) {
  const t = useTerminology()
  const { toast } = useToast()
  const tomorrow = addDays(new Date(), 1)

  const formSchema = z.object({
    appointment_date: z
      .date()
      .max(tomorrow, { message: "Appointment date cannot be in the future" })
      .min(
        // why two months? Can't imagine doctors are entering notes from two months ago
        subMonths(new Date(), 2),
        "Appointment date cannot be more than 2 months in the past",
      ),
    patient_name: z.string(),
    date_of_birth: z.date().max(tomorrow, {
      message: "Date of birth cannot be in the future",
    }),
    // most of the file validation is done within the dropzone component
    file: z.instanceof(File, {
      message: "Please select a valid audio or video file",
    }),
  })

  const form = useForm<UploadRecordingFormValues>({
    resolver: zodResolver(formSchema),
  })

  // the upload process took an embarrasing amount of time to figure out, mess with it at your own risk
  async function onSubmit(values: z.infer<typeof formSchema>) {
    const result = await submitVisitAudioUpload({
      appointmentDate: values.appointment_date.toISOString(),
      dateOfBirth: values.date_of_birth.toISOString(),
      file: values.file,
      patientName: values.patient_name,
    })

    if (!result.ok) {
      Sentry.captureMessage("visit audio upload failed", {
        tags: { upload_stage: result.stage },
      })
      toast({
        title: "Upload failed",
        description:
          "Your recording was not submitted. Please check your connection and try again.",
        variant: "destructive",
      })
      return
    }

    toast({
      title: "Upload complete",
      description:
        "Your recording has been uploaded successfully. Check back in a couple minutes.",
    })

    closeDialogBox()
  }

  // TODO should move a project constant
  const DATE_PLACEHOLDER =
    "Enter date (i.e. 'August 10, 2023') or pick from the calendar"

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
                <DateTimePicker
                  {...field}
                  showTimePicker={false}
                  suggestions={[
                    "Today",
                    "Yesterday",
                    "Last Friday",
                    "Two weeks ago",
                  ]}
                  placeholder={DATE_PLACEHOLDER}
                  autoFocus={false}
                />
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
              <FormLabel>{t.patientTitle} Name</FormLabel>
              <FormControl>
                <PatientComboboxSearch {...field} form={form} />
              </FormControl>
              <FormDescription>
                Search for an existing {t.patient}, or just type in a new{" "}
                {t.patientPossessive} name to create a new {t.patient}.
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
                <DateTimePicker
                  {...field}
                  showTimePicker={false}
                  placeholder={DATE_PLACEHOLDER}
                  // no suggestions for DOB
                  suggestions={[]}
                />
              </FormControl>
              <FormDescription>
                Use an accurate DOB. This will be required to identify the
                patient in the future.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="file"
          render={({ field }) => (
            <FormItem>
              <FormDescription>
                Upload an audio recording from a {t.patient} visit.
              </FormDescription>
              <FormControl>
                <AudioVideoDropzone
                  onDropFile={(file: File) => form.setValue("file", file)}
                  file={form.watch("file")}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit" disabled={form.formState.isSubmitting}>
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

// button which triggers the upload dialog to be opened
export function UploadRecordingButton() {
  const t = useTerminology()
  const [open, setOpen] = useState(false)

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
          {/* onOpenAutoFocus is a very important line! Prevents the automatic date popup */}
          <DialogContent onOpenAutoFocus={(e) => e.preventDefault()}>
            <DialogHeader>
              <DialogTitle>Upload Recording</DialogTitle>
            </DialogHeader>
            <VisitAudioUploadForm closeDialogBox={() => setOpen(false)} />
          </DialogContent>
        </Dialog>
        <TooltipContent>
          Upload an audio recording of a {t.patient} visit
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
