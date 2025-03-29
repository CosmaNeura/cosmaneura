import Markdown from "react-markdown"
import type { MetaFunction } from "react-router"

import { Route } from "./+types/detail"

import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { StatusBadge } from "~/components/shared/StatusBadge"
import { intakeDetail } from "~/configuration/client"
import { standardDateTimeFormat } from "~/lib/date"

import { invariant } from "@epic-web/invariant"

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

function EmptyState(intake: Intake) {
  return (
    <div className="mb-4">
      {/* <div className="mb-2 text-xl font-semibold">Notes</div> */}
      <div className="text-muted-foreground text-sm italic">
        Your patient has not yet completed their intake.
      </div>
    </div>
  )
}

export default function IntakeDetailPage({ loaderData }: Route.ComponentProps) {
  const { appointment, intake, patient } = loaderData
  const hasSummary = !!intake.summary

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

        {!hasSummary && <EmptyState />}
      </InnerScrollableContent>
    </SidebarInset>
  )
}
