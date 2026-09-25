import { data } from "react-router"

import { noteDetail } from "~/configuration/client"
import { isBillingRecoveryRedirectError } from "~/lib/billing-recovery"
import {
  practiceAgreementBlockFromClientResult,
  practiceAgreementBlockFromUnknown,
} from "~/lib/practice-gate-error"

export const NOTE_DETAIL_TIMEOUT_MS = 30_000

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
  const controller = new AbortController()
  let timeout: ReturnType<typeof setTimeout> | undefined
  let cancel = () => {}
  let result: Awaited<ReturnType<typeof noteDetail>>
  try {
    const deadline = new Promise<never>((_, reject) => {
      cancel = () => {
        controller.abort()
        reject(
          request.signal.reason ?? new DOMException("Cancelled", "AbortError"),
        )
      }
      request.signal.addEventListener("abort", cancel, { once: true })
      if (request.signal.aborted) {
        cancel()
        return
      }
      timeout = setTimeout(() => {
        // Reject before aborting the transport so both paths have safe UI copy.
        reject(new Error("Visit request deadline reached"))
        controller.abort()
      }, NOTE_DETAIL_TIMEOUT_MS)
    })
    result = await Promise.race([
      deadline,
      request.signal.aborted
        ? new Promise<never>(() => {})
        : noteDetail({
            throwOnError: false,
            signal: controller.signal,
            path: { appointment_id: id },
          }),
    ])
  } catch (error) {
    if (request.signal.aborted || isBillingRecoveryRedirectError(error))
      throw error
    const blocked = practiceAgreementBlockFromUnknown(error)
    if (blocked) throw data(blocked, { status: 403 })
    throw loadFailure()
  } finally {
    clearTimeout(timeout)
    request.signal.removeEventListener("abort", cancel)
  }

  const blocked = practiceAgreementBlockFromClientResult(result)
  if (blocked) throw data(blocked, { status: 403 })
  if (!result.data || (result.response && !result.response.ok)) {
    throw loadFailure(result.response?.status)
  }
  return result.data
}
