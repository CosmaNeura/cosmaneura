import { data } from "react-router"

import { scheduleList } from "~/configuration/client"
import { isBillingRecoveryRedirectError } from "~/lib/billing-recovery"
import {
  practiceAgreementBlockFromClientResult,
  practiceAgreementBlockFromUnknown,
} from "~/lib/practice-gate-error"
import { withReadDeadline } from "~/lib/read-deadline"

export type ScheduleLoadFailure = "unavailable" | "session" | "temporary"

export function scheduleLoadFailure(kind: ScheduleLoadFailure) {
  return data(
    { scheduleLoadFailure: kind },
    { status: kind === "session" ? 401 : kind === "unavailable" ? 404 : 503 },
  )
}

/** Keep unavailable schedules distinct from a successful list missing a meeting. */
export async function loadSchedule(request: Request) {
  let result: Awaited<ReturnType<typeof scheduleList>>
  try {
    result = await withReadDeadline(
      (signal) => scheduleList({ signal, throwOnError: false }),
      { signal: request.signal },
    )
  } catch (error) {
    if (request.signal.aborted || isBillingRecoveryRedirectError(error))
      throw error
    const blocked = practiceAgreementBlockFromUnknown(error)
    if (blocked) throw data(blocked, { status: 403 })
    throw scheduleLoadFailure("temporary")
  }

  // The SDK can return a redirect or transport failure when throwOnError=false.
  if (isBillingRecoveryRedirectError(result.error)) throw result.error
  const blocked = practiceAgreementBlockFromClientResult(result)
  if (blocked) throw data(blocked, { status: 403 })
  if (!result.data || (result.response && !result.response.ok)) {
    throw scheduleLoadFailure(
      result.response?.status === 401 ? "session" : "temporary",
    )
  }
  return result.data
}
