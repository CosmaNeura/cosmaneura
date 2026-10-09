import { addDays, format, parseISO, subMonths } from "date-fns"
import { useState } from "react"
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
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
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
import { ComboboxItemBase } from "~/components/vendor/Combobox"
import { Patient, client } from "~/configuration/client"
import { useToast } from "~/hooks/use-toast"
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

// Birthdays are calendar dates, even when an API datetime includes a timezone.
function patientBirthDate(value: string) {
  return parseISO(value.slice(0, 10))
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
  const t = useTerminology()
  const { data } = useQuery({ ...patientListOptions({ client }) })
  const patients = data ?? []

  if (!data) {
    return (
      <div role="status" aria-label={`Loading ${t.patientPlural}`}>
        <Skeleton className="h-9 w-full" />
      </div>
    )
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
      onChange(item.label.trim())
    } else {
      const selected_patient_id = item.value
      const patient = patients.find(
        (patient) => patient.id === selected_patient_id,
      )
      invariant(patient, "patient should exist if selected from combobox")

      // update the patient name field with the name of the patient, not the ID
      onChange(patient.name)

      if (patient.date_of_birth) {
        form.setValue("date_of_birth", patientBirthDate(patient.date_of_birth))
      } else {
        // This legacy upload still requires a date for identity matching. Never
        // carry the previous patient's date into a patient whose date is unknown.
        form.resetField("date_of_birth")
      }
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
                {patientItem.date_of_birth
                  ? format(
                      patientBirthDate(patientItem.date_of_birth),
                      "M/d/yy",
                    )
                  : "Date of birth not provided"}
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

// Progress covers the file transfer; the short steps before (requesting an
// upload slot) and after (registering the visit) are named instead.
export function UploadProgress({ fraction }: { fraction: number }) {
  // Floor so the bar never claims 100% while bytes are still being sent.
  const percent = Math.floor(fraction * 100)
  const label =
    fraction <= 0
      ? "Preparing upload…"
      : fraction >= 1
        ? "Finishing up…"
        : `Uploading… ${percent}%`
  // Screen readers hear quarter steps rather than every percent.
  const announced =
    fraction > 0 && fraction < 1
      ? `Uploading, ${Math.floor(percent / 25) * 25}%`
      : label
  return (
    <div className="space-y-2">
      {/* The shared Progress wrapper keeps `value` for its indicator and does
          not forward it to Radix (components/ui is vendored and not edited
          here), so the ARIA values are supplied at the call site. */}
      <Progress
        value={percent}
        aria-label="Upload progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      />
      <p className="text-muted-foreground text-sm" aria-hidden="true">
        {label}
      </p>
      <p className="sr-only" aria-live="polite">
        {announced}
      </p>
    </div>
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
    // Validate without trimming: the backend matches existing patients by
    // their exact stored name, so a selected name must pass through as-is.
    patient_name: z
      .string({ required_error: `Enter the ${t.patientPossessive} name` })
      .refine(
        (name) => name.trim().length > 0,
        `Enter the ${t.patientPossessive} name`,
      ),
    date_of_birth: z
      .date({
        required_error: "Enter a date of birth",
        invalid_type_error: "Enter a date of birth",
      })
      .max(tomorrow, {
        message: "Date of birth cannot be in the future",
      }),
    // most of the file validation is done within the dropzone component
    file: z.instanceof(File, {
      message: "Choose a recording to upload",
    }),
  })

  const form = useForm<UploadRecordingFormValues>({
    resolver: zodResolver(formSchema),
  })
  const [uploadProgress, setUploadProgress] = useState(0)

  // the upload process took an embarrasing amount of time to figure out, mess with it at your own risk
  async function onSubmit(values: z.infer<typeof formSchema>) {
    setUploadProgress(0)
    const result = await submitVisitAudioUpload({
      onUploadProgress: setUploadProgress,
      // Encounters retain their instant and ordering; birthdays are calendar days.
      appointmentDate: values.appointment_date.toISOString(),
      dateOfBirth: format(values.date_of_birth, "yyyy-MM-dd'T'00:00:00"),
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
                  showTimePicker
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
                Use an accurate date of birth. It is used to identify this{" "}
                {t.patient} in the future.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="file"
          render={() => (
            <FormItem>
              <FormDescription>
                Upload an audio recording from a {t.patient} visit.
              </FormDescription>
              <FormControl>
                <AudioVideoDropzone
                  onDropFile={(file: File) =>
                    form.setValue("file", file, { shouldValidate: true })
                  }
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
        {form.formState.isSubmitting && (
          <UploadProgress fraction={uploadProgress} />
        )}
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
              <Button
                variant="outline"
                size="sm"
                className="size-9 md:h-8 md:w-auto"
              >
                <Upload className="h-4 w-4 md:mr-2" aria-hidden="true" />
                {/* Icon-only on phones; the name stays available to assistive tech. */}
                <span className="sr-only md:not-sr-only">Upload Recording</span>
              </Button>
            </DialogTrigger>
          </TooltipTrigger>
          {/* onOpenAutoFocus is a very important line! Prevents the automatic date popup */}
          <DialogContent
            className="max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] overflow-y-auto rounded-lg"
            onOpenAutoFocus={(e) => e.preventDefault()}
          >
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
