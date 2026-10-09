import Markdown from "react-markdown"
import type { MetaFunction } from "react-router"

import { Route } from "./+types/detail"

import { SidebarInset } from "@/components/ui/sidebar"
import { CopyButton } from "~/components/shared/CopyButton"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { PageHeader } from "~/components/shared/PageHeader"
import { StatusBadge } from "~/components/shared/StatusBadge"
import { intakeDetail } from "~/configuration/client"
import { standardDateTimeFormat } from "~/lib/date"
import { intakeStatusBadge } from "~/lib/intake-status"
import { useTerminology } from "~/utils/terminology"

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
      content: "Intake summary",
    },
  ]
}

export function IntakeHeader({
  badge,
  title,
  dataFields,
}: {
  badge: ReturnType<typeof intakeStatusBadge>
  title: string
  dataFields: Record<string, string>
}) {
  return (
    <div className="mb-6 flex items-start justify-between">
      <div className="text-left">
        <div className="flex items-center gap-3">
          <h1
            className="text-2xl font-semibold"
            data-ph-no-capture=""
            data-sentry-block=""
          >
            {title}
          </h1>
          <StatusBadge status={badge.status} label={badge.label} />
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

export function EmptyState({
  intake,
}: {
  intake: { public_url: string | null }
}) {
  const t = useTerminology()
  return (
    <section
      aria-labelledby="intake-waiting-title"
      className="bg-muted/40 max-w-xl rounded-lg border p-5"
    >
      <h2 id="intake-waiting-title" className="text-base font-semibold">
        Waiting on your {t.patient}
      </h2>
      <p className="text-muted-foreground mt-1 text-sm">
        They haven&apos;t finished their intake yet. If they need the link
        again, you can copy it and send it to them.
      </p>
      {intake.public_url ? (
        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
          <a
            target="_blank"
            rel="noopener noreferrer"
            href={intake.public_url}
            data-ph-no-capture=""
            data-sentry-block=""
            className="text-primary min-w-0 truncate text-sm underline underline-offset-4"
          >
            {intake.public_url}
          </a>
          <CopyButton
            text={intake.public_url}
            label="Copy intake link"
            variant="default"
            className="shrink-0"
          />
        </div>
      ) : null}
    </section>
  )
}

export default function IntakeDetailPage({ loaderData }: Route.ComponentProps) {
  const t = useTerminology()
  const { intake } = loaderData
  const hasSummary = !!intake.summary

  return (
    <SidebarInset>
      <PageHeader title={loaderData.patient.name} />
      <InnerScrollableContent>
        <IntakeHeader
          title={loaderData.patient.name}
          // A written summary is the ground truth for completion, even if a
          // re-run summary left an answer missing.
          badge={intakeStatusBadge(
            hasSummary ? "complete" : loaderData.appointment.intake_status,
            t,
          )}
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

        {!hasSummary && <EmptyState intake={intake} />}
      </InnerScrollableContent>
    </SidebarInset>
  )
}
