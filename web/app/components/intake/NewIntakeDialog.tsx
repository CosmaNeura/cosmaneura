import { useState } from "react"
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
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "~/components/ui/form"
import { Input } from "~/components/ui/input"
import { createManualIntake } from "~/configuration/client"
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

/**
 * One-click intake: create a patient/appointment/intake directly (no external
 * scheduling sync required) and email the chat link to the patient.
 */
export function NewIntakeDialog() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const revalidator = useRevalidator()

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: {
      patient_name: "",
      date_of_birth: "",
      email: "",
      appointment_date: "",
    },
  })

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
    revalidator.revalidate()
    navigate(href("/intake/:id", { id: String(data.appointment_id) }))
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary" aria-label="New intake">
          <Plus className="mr-1 h-4 w-4" />
          New
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Send an Intake</DialogTitle>
          <DialogDescription>
            Create an intake for an upcoming appointment and email your patient
            the chat link — no scheduling system required.
          </DialogDescription>
        </DialogHeader>
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
