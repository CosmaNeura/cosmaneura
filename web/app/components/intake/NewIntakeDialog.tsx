import { ReactNode, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { href, useNavigate, useRevalidator } from "react-router"
import { z } from "zod"

import { Plus } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Icons } from "~/components/icons"
import { SimpleComboBox } from "~/components/shared/SimpleComboBox"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "~/components/ui/form"
import { Input } from "~/components/ui/input"
import type { ComboboxItemBase } from "~/components/vendor/Combobox/types"
import {
  Patient,
  createManualIntake,
  patientList,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"

import { zodResolver } from "@hookform/resolvers/zod"

const formSchema = z.object({
  patient_name: z.string().min(1, "Patient name is required"),
  date_of_birth: z.string().min(1, "Date of birth is required"),
  email: z.string().email("A valid email is required"),
  appointment_date: z
    .string()
    .min(1, "Appointment date is required")
    .refine(
      (value) => new Date(value) > new Date(),
      "The appointment must be in the future",
    ),
})

export interface NewIntakeDefaults {
  patientName?: string
  dateOfBirth?: string
  email?: string
  /** ISO timestamp; converted to the browser's local time for the input */
  appointmentDate?: string
}

/** format an ISO timestamp for an <input type="datetime-local"> in local time */
function toDatetimeLocal(iso: string | undefined): string {
  if (!iso) return ""

  const date = new Date(iso)
  if (isNaN(date.getTime())) return ""

  const pad = (part: number) => String(part).padStart(2, "0")
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

/**
 * One-click intake: create a patient/appointment/intake directly (no external
 * scheduling sync required) and email the chat link to the patient.
 *
 * Fields can be pre-populated two ways: `defaultValues` from the calling page
 * (e.g. the schedule page passes the meeting's start time), and the
 * returning-patient picker, which fills name/DOB/email from the practice's own
 * patient records.
 */
export function NewIntakeDialog({
  trigger,
  defaultValues,
}: {
  trigger?: ReactNode
  defaultValues?: NewIntakeDefaults
}) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const revalidator = useRevalidator()

  const [patients, setPatients] = useState<Patient[]>([])
  const [selectedPatientId, setSelectedPatientId] = useState<string | null>(
    null,
  )

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: {
      patient_name: defaultValues?.patientName ?? "",
      date_of_birth: defaultValues?.dateOfBirth ?? "",
      email: defaultValues?.email ?? "",
      appointment_date: toDatetimeLocal(defaultValues?.appointmentDate),
    },
  })

  // load the returning-patient list only once the dialog is actually opened
  useEffect(() => {
    if (!open || patients.length > 0) return

    let cancelled = false

    patientList().then(({ data }) => {
      if (!cancelled && data) {
        setPatients(data)
      }
    })

    return () => {
      cancelled = true
    }
  }, [open, patients.length])

  function applyPatient(item: ComboboxItemBase | null) {
    const patient = patients.find((entry) => String(entry.id) === item?.value)
    if (!patient) return

    setSelectedPatientId(String(patient.id))
    form.setValue("patient_name", patient.name, { shouldDirty: true })
    if (patient.date_of_birth) {
      form.setValue("date_of_birth", patient.date_of_birth.slice(0, 10), {
        shouldDirty: true,
      })
    }
    form.setValue("email", patient.email ?? "", { shouldDirty: true })
  }

  async function onSubmit(values: z.infer<typeof formSchema>) {
    const { data, error } = await createManualIntake({
      body: {
        patient_name: values.patient_name,
        date_of_birth: values.date_of_birth,
        email: values.email,
        // datetime-local values have no timezone; convert from the browser's
        // local time so the backend receives an unambiguous timestamp
        appointment_date: new Date(values.appointment_date).toISOString(),
      },
    })

    if (error || !data) {
      toast({
        title: "Could Not Create Intake",
        description: "Check the details and try again.",
        variant: "destructive",
      })
      return
    }

    toast({
      title: "Intake Link Sent",
      description: data.email_sent_to
        ? `The intake link was emailed to ${data.email_sent_to}.`
        : "The intake was created.",
    })

    setOpen(false)
    form.reset()
    setSelectedPatientId(null)
    revalidator.revalidate()
    navigate(href("/intake/:id", { id: String(data.appointment_id) }))
  }

  const patientItems: ComboboxItemBase[] = patients
    .filter((patient) => patient.id)
    .map((patient) => ({
      value: String(patient.id),
      label: patient.date_of_birth
        ? `${patient.name} · DOB ${patient.date_of_birth.slice(0, 10)}`
        : patient.name,
    }))

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger ?? (
          <Button size="sm" variant="secondary" aria-label="New intake">
            <Plus className="mr-1 h-4 w-4" />
            New
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Send an Intake</DialogTitle>
          <DialogDescription>
            Create an intake for an upcoming appointment and email your patient
            the chat link — no scheduling system required.
          </DialogDescription>
        </DialogHeader>

        {patientItems.length > 0 && (
          <div className="space-y-1">
            <p className="text-sm font-medium">Returning patient (optional)</p>
            <SimpleComboBox
              items={patientItems}
              value={selectedPatientId}
              onSelectedItemChange={applyPatient}
              placeholder="Search your patients to fill their details..."
            />
          </div>
        )}

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="patient_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Patient Name</FormLabel>
                  <FormControl>
                    <Input placeholder="Jane Doe" {...field} />
                  </FormControl>
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
                    <Input type="date" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Patient Email</FormLabel>
                  <FormControl>
                    <Input
                      type="email"
                      placeholder="jane@example.com"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="appointment_date"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Appointment Date &amp; Time</FormLabel>
                  <FormControl>
                    <Input type="datetime-local" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? (
                  <>
                    <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                    Sending...
                  </>
                ) : (
                  "Create & Email Link"
                )}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
