import Markdown from "react-markdown"
import type { MetaFunction } from "react-router"

import { Route } from "./+types/detail"

import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { CopyButton } from "~/components/shared/CopyButton"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { StatusBadge } from "~/components/shared/StatusBadge"
import {
  AnswerList,
  answeredQuestions,
  prepStatusFor,
} from "~/components/visit-prep/AnswerList"
import { Intake, intakeDetail } from "~/configuration/client"
import { standardDateTimeFormat } from "~/lib/date"
import { useTerminology } from "~/utils/terminology"

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
  const t = useTerminology()
  return (
    <header className="bg-background sticky top-0 z-10 flex shrink-0 items-center gap-2 border-b p-4">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <h1 className="text-lg font-semibold">
        {title ? title : `Select a ${t.patient}`}
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

function NotStartedState({ intake }: { intake: Intake }) {
  const t = useTerminology()
  return (
    <div className="mb-4">
      <div className="text-muted-foreground">
        Your {t.patient} has not answered anything yet. If you'd like to resend
        their link, you can find it below.
        <br />
        <br />
        <div className="flex items-center gap-2">
          <a target="_blank" href={intake.public_url} className="text-blue-500">
            {intake.public_url}
          </a>
          <CopyButton text={intake.public_url} />
        </div>
      </div>
    </div>
  )
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-muted-foreground mb-3 text-xs font-semibold tracking-wide uppercase">
      {children}
    </h2>
  )
}

export default function IntakeDetailPage({ loaderData }: Route.ComponentProps) {
  const { intake } = loaderData
  const hasBrief = !!intake.summary
  const answers = answeredQuestions(intake.intake_questions)

  const status = prepStatusFor(answers, hasBrief)

  return (
    <SidebarInset>
      <Header title={loaderData.patient.name} />
      <InnerScrollableContent>
        <IntakeHeader
          title={loaderData.patient.name}
          status={status}
          dataFields={{
            "Appointment Date": standardDateTimeFormat(
              loaderData.appointment.appointment_date,
            ),
          }}
        />

        {hasBrief && (
          <section className="mb-8">
            <SectionHeading>Before the visit</SectionHeading>
            <div className="prose-headings prose max-w-full">
              <Markdown>{intake.summary}</Markdown>
            </div>
          </section>
        )}

        {answers.length > 0 && (
          <section className="mb-4">
            <SectionHeading>
              {hasBrief
                ? "In their own words"
                : `Answered so far · ${answers.length}`}
            </SectionHeading>
            {!hasBrief && (
              <p className="text-muted-foreground mb-5 text-sm">
                Still in progress. The brief is written once they finish.
              </p>
            )}
            <AnswerList answers={answers} />
          </section>
        )}

        {status === "not-started" && <NotStartedState intake={intake} />}
      </InnerScrollableContent>
    </SidebarInset>
  )
}
