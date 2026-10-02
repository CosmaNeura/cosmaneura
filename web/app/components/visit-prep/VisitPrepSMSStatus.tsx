import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type VisitPrepSettingsData,
  type VisitPrepSmsStatusResponse,
  visitPrepSmsStatus,
} from "~/configuration/client"
import {
  VIEW_AS_HARD_BLOCK_EVENT,
  subscribeToViewAsStateChanges,
} from "~/lib/view-as-binding"

import { validEmailStatus } from "./VisitPrepEmailStatus"

const steps = {
  booking_sms: "Booking invitation",
  unopened_48h_sms: "48-hour unopened reminder",
  unfinished_12h_sms: "12-hour unfinished reminder",
}
const labels = {
  not_requested: "No text requested",
  queued: "Text queued at last load",
  submitting: "Submission claimed — outcome not recorded",
  accepted: "SMS service accepted the text",
  failed: "Text failed before confirmed acceptance",
  suppressed: "Text suppressed",
  uncertain: "Submission outcome uncertain — do not resend",
}
const reasons = {
  transport_unavailable: "SMS transport unavailable",
  scope_unavailable: "Required authority unavailable",
  obsolete_context: "Invitation context obsolete",
  expired: "Sending deadline passed",
  submission_uncertain: "No reliable submission outcome",
}
const fields = [
  "step",
  "status",
  "outcome_code",
  "credential_generation",
  "context_current",
  "invitation_expired",
  "submission_window_elapsed",
  "requested_at",
  "expires_at",
  "submitted_at",
  "finalized_at",
]
export function validSMSStatus(
  value: VisitPrepSmsStatusResponse,
  id: string,
): boolean {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length !== 6 ||
    ![
      "contract",
      "preparation_id",
      "current_credential_generation",
      "messages",
      "checked_at",
      "dispatch_integration",
    ].every((k) => Object.hasOwn(value, k)) ||
    value.contract !== "visit_prep.sms_status.v1" ||
    value.preparation_id !== id ||
    value.dispatch_integration !== "not_connected" ||
    !Array.isArray(value.messages) ||
    value.messages.length !== 3
  )
    return false
  return value.messages.every((message, index) => {
    if (
      !message ||
      typeof message !== "object" ||
      Array.isArray(message) ||
      Object.keys(message).length !== fields.length ||
      !fields.every((k) => Object.hasOwn(message, k)) ||
      message.step !== Object.keys(steps)[index] ||
      (message.outcome_code !== null &&
        !Object.hasOwn(reasons, message.outcome_code))
    )
      return false
    const { step: _step, ...snapshot } = message
    // Reuse the existing closed timestamp/state validator, not email authority.
    // SMS's outer contract, ordered steps and narrower outcome codes are checked above.
    return (
      validEmailStatus(
        {
          ...snapshot,
          contract: "visit_prep.email_status.v1",
          preparation_id: id,
          current_credential_generation: value.current_credential_generation,
          checked_at: value.checked_at,
          dispatch_integration: "default_off_worker",
        },
        id,
      ) &&
      (message.requested_at === null ||
        Date.parse(message.expires_at!) - Date.parse(message.requested_at) <=
          86_400_000)
    )
  })
}

export function VisitPrepSMSStatus({
  preparationId,
  access,
  scopeKey,
}: {
  preparationId: string
  access: VisitPrepSettingsData
  scopeKey: string
}) {
  const allowed =
    access.eligible &&
    access.can_manage &&
    Boolean(scopeKey) &&
    Boolean(preparationId)
  const epoch = useRef(0)
  const pending = useRef(false)
  const [scope, setScope] = useState(scopeKey)
  const [blocked, setBlocked] = useState(false)
  const [value, setValue] = useState<VisitPrepSmsStatusResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    function clear() {
      epoch.current += 1
      pending.current = false
      setScope(scopeKey)
      setValue(null)
      setBusy(false)
      setError(null)
    }
    function block() {
      clear()
      setBlocked(true)
    }
    function conceal() {
      if (document.visibilityState === "hidden") clear()
    }
    clear()
    window.addEventListener("blur", clear)
    window.addEventListener("pagehide", clear)
    document.addEventListener("visibilitychange", conceal)
    window.addEventListener(VIEW_AS_HARD_BLOCK_EVENT, block)
    const unsubscribe = subscribeToViewAsStateChanges(block)
    return () => {
      epoch.current += 1
      window.removeEventListener("blur", clear)
      window.removeEventListener("pagehide", clear)
      document.removeEventListener("visibilitychange", conceal)
      window.removeEventListener(VIEW_AS_HARD_BLOCK_EVENT, block)
      unsubscribe()
    }
  }, [allowed, scopeKey, preparationId])
  async function load() {
    if (!allowed || blocked || pending.current) return
    const current = ++epoch.current
    pending.current = true
    setBusy(true)
    setValue(null)
    setError(null)
    try {
      const result = await visitPrepSmsStatus({
        path: { prep_id: preparationId },
        cache: "no-store",
      })
      if (epoch.current !== current) return
      if (
        result.error ||
        !result.data ||
        !validSMSStatus(result.data, preparationId)
      )
        throw new Error("unavailable")
      setScope(scopeKey)
      setValue(result.data)
    } catch {
      if (epoch.current === current)
        setError(
          "SMS status is unavailable. Reload to check; a failed read does not mean a text failed.",
        )
    } finally {
      if (epoch.current === current) {
        pending.current = false
        setBusy(false)
      }
    }
  }
  if (!allowed || blocked)
    return <p>SMS status is unavailable in this session.</p>
  return (
    <section
      className="min-w-0 space-y-3 rounded-md border p-4"
      aria-labelledby="visit-prep-sms-status-title"
    >
      <h3 id="visit-prep-sms-status-title" className="font-semibold">
        Invitation and reminder SMS status
      </h3>
      <Button variant="outline" disabled={busy} onClick={() => void load()}>
        Load / reload SMS status
      </Button>
      {busy && <p role="status">Loading SMS status…</p>}
      {error && <p role="alert">{error}</p>}
      {value &&
        scope === scopeKey &&
        value.preparation_id === preparationId && (
          <>
            <p className="text-sm">
              This is the recorded state at last load, not confirmed recipient
              delivery or permission to send. The real SMS service and automated
              booking/reminder dispatch are not connected. No text is sent by
              this screen.
            </p>
            <p className="text-sm">
              Service acceptance does not prove the patient received or opened a
              text.
            </p>
            {value.messages.map((message) => (
              <section
                key={message.step}
                className="min-w-0 space-y-2 rounded-md border p-3"
              >
                <h4 className="font-medium">{steps[message.step]}</h4>
                <p>{labels[message.status]}</p>
                {message.outcome_code && (
                  <p className="text-sm">{reasons[message.outcome_code]}</p>
                )}
                {["submitting", "uncertain"].includes(message.status) && (
                  <p className="text-sm">
                    Do not resend: the original attempt may already have reached
                    the SMS service.
                  </p>
                )}
                {message.context_current === false && (
                  <p className="text-sm">
                    This text belongs to an obsolete or inactive preparation
                    context.
                  </p>
                )}
                {message.invitation_expired && (
                  <p className="text-sm">
                    The text’s sending deadline has passed.
                  </p>
                )}
                {message.submission_window_elapsed && (
                  <p className="text-sm">
                    The submission window has elapsed; final reconciliation is
                    not recorded.
                  </p>
                )}
                {message.requested_at && (
                  <details className="text-muted-foreground text-xs">
                    <summary>Recorded times and invitation generation</summary>
                    <dl className="mt-2 space-y-1 break-all">
                      <dt>Invitation generation</dt>
                      <dd>
                        {message.credential_generation} (current:{" "}
                        {value.current_credential_generation})
                      </dd>
                      <dt>Requested at (UTC)</dt>
                      <dd>{new Date(message.requested_at).toISOString()}</dd>
                      <dt>Sending deadline (UTC)</dt>
                      <dd>{new Date(message.expires_at!).toISOString()}</dd>
                      {message.submitted_at && (
                        <>
                          <dt>Submission claimed at (UTC)</dt>
                          <dd>
                            {new Date(message.submitted_at).toISOString()}
                          </dd>
                        </>
                      )}
                      {message.finalized_at && (
                        <>
                          <dt>Outcome recorded at (UTC)</dt>
                          <dd>
                            {new Date(message.finalized_at).toISOString()}
                          </dd>
                        </>
                      )}
                    </dl>
                  </details>
                )}
              </section>
            ))}
            <p className="text-muted-foreground text-xs break-all">
              Checked at (UTC): {new Date(value.checked_at).toISOString()}
            </p>
          </>
        )}
    </section>
  )
}
