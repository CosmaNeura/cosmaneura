import { useState } from "react"
import Markdown from "react-markdown"
import { useRevalidator } from "react-router"
import type { MetaFunction } from "react-router"

import { Send, TriangleAlert } from "lucide-react"

import { Route } from "./+types/detail"

import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { Icons } from "~/components/icons"
import { CopyButton } from "~/components/shared/CopyButton"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { StatusBadge } from "~/components/shared/StatusBadge"
import { Input } from "~/components/ui/input"
import { Intake, intakeDetail, sendIntakeLink } from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { standardDateTimeFormat } from "~/lib/date"

import { invariant } from "@epic-web/invariant"

type Status = Parameters<typeof StatusBadge>[0]["status"]

export async function clientLoader(loaderArgs: Route.ClientLoaderArgs) {
  const { data } = await intakeDetail({
    path: {
      appointment_id: loaderArgs.params.id,
    },
  })

  // TODO need idiomatic error handling here
  invariant(data, "data should exist")

  return data
}

// TODO can we make this dynamic?
export const meta: MetaFunction = () => {
  return [
    { title: "Intake" },
    {
      name: "description",
      content: "Your patient meeting notes",
    },
  ]
}

function Header({ title }: { title?: string }) {
  return (
    <header className="bg-background sticky top-0 z-10 flex shrink-0 items-center gap-2 border-b p-4">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <h1 className="text-lg font-semibold">
        {title ? title : "Select a patient"}
      </h1>
    </header>
  )
}

function IntakeHeader({
  status,
  title,
  dataFields,
}: {
  status: Status
  title: string
  dataFields: Record<string, string>
}) {
  return (
    <div className="mb-6 flex items-start justify-between">
      <div className="text-left">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">{title}</h1>
          <StatusBadge status={status} />
        </div>
        <div className="mt-1 space-y-1">
          {Object.entries(dataFields).map(([key, value]) => (
            <p className="text-sm" key={key}>
              <span className="text-muted-foreground font-medium">{key}:</span>{" "}
              <span>{value}</span>
            </p>
          ))}
        </div>
      </div>
    </div>
  )
}

/**
 * AI-assembled profile from records the practice already holds (prior visit
 * notes + previous intake answers). Deliberately presented as needing
 * verification: items can be stale or patient-reported and unconfirmed.
 */
function PatientContextCard({ patientContext }: { patientContext: string }) {
  return (
    <section className="mt-8 rounded-lg border border-amber-400 bg-amber-50 p-4">
      <div className="flex items-center gap-2">
        <TriangleAlert className="h-4 w-4 text-amber-600" />
        <h2 className="font-semibold">Patient Context — Verify Before Use</h2>
      </div>
      <p className="text-muted-foreground mt-1 text-sm">
        Assembled by AI from this patient's prior visit notes and self-reported
        intake answers already in your records. Each item is labeled with its
        source and age. Items marked "unconfirmed" are patient-reported and have
        not been clinically verified — validate anything you rely on.
      </p>
      <div className="prose-headings prose mt-3 max-w-full text-sm">
        <Markdown>{patientContext}</Markdown>
      </div>
    </section>
  )
}

function SendLinkForm({
  appointmentId,
  patientEmail,
}: {
  appointmentId: string
  patientEmail: string | null | undefined
}) {
  const [email, setEmail] = useState(patientEmail ?? "")
  const [isSending, setIsSending] = useState(false)
  const revalidator = useRevalidator()

  async function send() {
    setIsSending(true)

    try {
      const { data, error } = await sendIntakeLink({
        path: { appointment_id: appointmentId },
        body: { email },
      })

      if (error || !data) {
        toast({
          title: "Could Not Send Intake Link",
          description: "Check the email address and try again.",
          variant: "destructive",
        })
        return
      }

      toast({
        title: "Intake Link Sent",
        description: `The intake link was emailed to ${data.email_sent_to}.`,
      })

      // the email is saved to the patient record, refresh so it stays pre-filled
      revalidator.revalidate()
    } finally {
      setIsSending(false)
    }
  }

  return (
    <div className="flex max-w-md items-center gap-2">
      <Input
        type="email"
        placeholder="patient@example.com"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
      />
      <Button onClick={send} disabled={isSending || !email}>
        {isSending ? (
          <>
            <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
            Sending...
          </>
        ) : (
          <>
            <Send className="mr-2 h-4 w-4" />
            Email Link
          </>
        )}
      </Button>
    </div>
  )
}

function EmptyState({
  intake,
  appointmentId,
  patientEmail,
}: {
  intake: Intake
  appointmentId: string
  patientEmail: string | null | undefined
}) {
  return (
    <div className="text-muted-foreground mb-4 space-y-4">
      <p>
        Your patient has not completed their intake yet. Email them the link
        below, or copy it and send it another way.
      </p>
      <SendLinkForm appointmentId={appointmentId} patientEmail={patientEmail} />
      <div className="flex items-center gap-2">
        <a target="_blank" href={intake.public_url} className="text-blue-500">
          {intake.public_url}
        </a>
        <CopyButton text={intake.public_url} />
      </div>
    </div>
  )
}

export default function IntakeDetailPage({ loaderData }: Route.ComponentProps) {
  const { intake } = loaderData
  const hasSummary = !!intake.summary

  const appointmentId = loaderData.appointment.id
  invariant(appointmentId, "appointment.id is required")

  return (
    <SidebarInset>
      <Header title={loaderData.patient.name} />
      <InnerScrollableContent>
        <IntakeHeader
          title={loaderData.patient.name}
          status={hasSummary ? "completed" : "not-started"}
          dataFields={{
            "Appointment Date": standardDateTimeFormat(
              loaderData.appointment.appointment_date,
            ),
          }}
        />
        {hasSummary && (
          <div className="prose-headings prose max-w-full">
            <Markdown>{intake.summary}</Markdown>
          </div>
        )}

        {hasSummary && intake.patient_context && (
          <PatientContextCard patientContext={intake.patient_context} />
        )}

        {!hasSummary && (
          <EmptyState
            intake={intake}
            appointmentId={appointmentId}
            patientEmail={loaderData.patient.email}
          />
        )}
      </InnerScrollableContent>
    </SidebarInset>
  )
}
