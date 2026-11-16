import { Link, isRouteErrorResponse, useRevalidator } from "react-router"

import { Button } from "@/components/ui/button"
import { ViewAsProtectedErrorGuard } from "~/components/ViewAsRouteGuard"
import { PracticeAgreementWall } from "~/components/notes/PracticeAgreementWall"
import { practiceAgreementBlockFromRouteFailure } from "~/lib/practice-gate-error"
import type { ScheduleLoadFailure } from "~/lib/schedule-load"

function ScheduleRecovery({
  kind,
  list,
}: {
  kind: ScheduleLoadFailure
  list: boolean
}) {
  const revalidator = useRevalidator()
  const pending = revalidator.state === "loading"
  const title =
    kind === "session"
      ? "Your session needs verification"
      : kind === "unavailable"
        ? list
          ? "Schedule unavailable"
          : "Meeting unavailable"
        : list
          ? "Couldn’t load your schedule"
          : "Couldn’t load this meeting"
  const detail =
    kind === "session"
      ? "Try again to verify your session, or return to your visits and sign in if prompted."
      : kind === "unavailable"
        ? list
          ? "This account cannot open this schedule. Return to your visits or try again."
          : "This meeting is no longer in your schedule or this account cannot open it. Return to your schedule to choose a current meeting."
        : "Check your connection and try again. Your saved visits have not changed."
  return (
    <main className="mx-auto w-full max-w-lg px-6 py-12" role="alert">
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="text-muted-foreground mt-3">{detail}</p>
      <div className="mt-6 flex flex-wrap gap-3">
        <Button
          type="button"
          disabled={pending}
          onClick={() => void revalidator.revalidate()}
        >
          {pending ? "Trying again…" : "Try again"}
        </Button>
        {!list && (
          <Button asChild variant="outline">
            <Link to="/schedule">Return to schedule</Link>
          </Button>
        )}
        <Button asChild variant="outline">
          <Link to="/notes">Return to visits</Link>
        </Button>
      </div>
    </main>
  )
}

/** Never mount meeting, patient or capture controls after a failed read. */
export function ScheduleLoadBoundary({
  error,
  list = false,
}: {
  error: unknown
  list?: boolean
}) {
  const blocked = practiceAgreementBlockFromRouteFailure(error)
  const kind = isRouteErrorResponse(error)
    ? error.data?.scheduleLoadFailure
    : undefined
  if (!blocked && !["unavailable", "session", "temporary"].includes(kind))
    throw error
  return (
    <ViewAsProtectedErrorGuard>
      {blocked ? (
        <PracticeAgreementWall block={blocked} />
      ) : (
        <ScheduleRecovery kind={kind} list={list} />
      )}
    </ViewAsProtectedErrorGuard>
  )
}
