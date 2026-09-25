import { data } from "react-router"

import { noteDetail } from "~/configuration/client"
import { isBillingRecoveryRedirectError } from "~/lib/billing-recovery"
import {
  practiceAgreementBlockFromClientResult,
  practiceAgreementBlockFromUnknown,
} from "~/lib/practice-gate-error"
import { READ_DEADLINE_MS, withReadDeadline } from "~/lib/read-deadline"

export const NOTE_DETAIL_TIMEOUT_MS = READ_DEADLINE_MS

export type NoteLoadFailure = "unavailable" | "session" | "temporary"

function loadFailure(status?: number) {
  const kind: NoteLoadFailure =
    status === 401
      ? "session"
      : status === 403 || status === 404 || status === 400 || status === 422
        ? "unavailable"
        : "temporary"
  return data(
    { noteLoadFailure: kind },
    { status: kind === "session" ? 401 : kind === "unavailable" ? 404 : 503 },
  )
}

/** Bound the full SDK read, including token acquisition and response parsing. */
export async function loadNoteDetail(id: string, request: Request) {
  let result: Awaited<ReturnType<typeof noteDetail>>
  try {
    result = await withReadDeadline(
      (signal) =>
        noteDetail({
          throwOnError: false,
          signal,
          path: { appointment_id: id },
        }),
      { signal: request.signal },
    )
  } catch (error) {
    if (request.signal.aborted || isBillingRecoveryRedirectError(error))
      throw error
    const blocked = practiceAgreementBlockFromUnknown(error)
    if (blocked) throw data(blocked, { status: 403 })
    throw loadFailure()
  }

  // The generated client can return a transport exception when throwOnError is
  // false. Retain the existing redirect signal in that shape as well.
  if (isBillingRecoveryRedirectError(result.error)) throw result.error
  const blocked = practiceAgreementBlockFromClientResult(result)
  if (blocked) throw data(blocked, { status: 403 })
  if (!result.data || (result.response && !result.response.ok)) {
    throw loadFailure(result.response?.status)
  }
  return result.data
}
