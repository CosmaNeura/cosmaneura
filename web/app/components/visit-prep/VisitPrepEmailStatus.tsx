import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type VisitPrepEmailStatusResponse,
  type VisitPrepSettingsData,
  visitPrepEmailStatus,
} from "~/configuration/client"
import {
  VIEW_AS_HARD_BLOCK_EVENT,
  subscribeToViewAsStateChanges,
} from "~/lib/view-as-binding"

const labels = {
  not_requested: "No email invitation requested",
  queued: "Invitation queued at last load",
  submitting: "Submission claimed — outcome not recorded",
  accepted: "Email provider accepted the invitation",
  failed: "Invitation failed before confirmed acceptance",
  suppressed: "Invitation suppressed",
  uncertain: "Submission outcome uncertain — do not resend",
}
const reasons = {
  transport_unavailable: "Email transport unavailable",
  message_invalid: "Invitation message invalid",
  scope_unavailable: "Required authority unavailable",
  obsolete_context: "Invitation context obsolete",
  consent_withdrawn: "Email consent withdrawn",
  expired: "Invitation expired",
  submission_uncertain: "No reliable submission outcome",
}
const fields = [
  "contract",
  "preparation_id",
  "status",
  "outcome_code",
  "current_credential_generation",
  "credential_generation",
  "context_current",
  "invitation_expired",
  "submission_window_elapsed",
  "requested_at",
  "expires_at",
  "submitted_at",
  "finalized_at",
  "checked_at",
  "dispatch_integration",
]
function timestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 64 &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(
      value,
    ) &&
    Number.isFinite(Date.parse(value))
  )
}
function generation(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value > 0 &&
    value <= 2_147_483_647
  )
}
export function validEmailStatus(
  value: VisitPrepEmailStatusResponse,
  id: string,
): boolean {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length !== fields.length ||
    !fields.every((key) => Object.hasOwn(value, key)) ||
    value.contract !== "visit_prep.email_status.v1" ||
    value.preparation_id !== id ||
    value.dispatch_integration !== "default_off_worker" ||
    !Object.hasOwn(labels, value.status) ||
    !generation(value.current_credential_generation) ||
    !timestamp(value.checked_at)
  )
    return false
  if (value.status === "not_requested")
    return [
      value.outcome_code,
      value.credential_generation,
      value.context_current,
      value.invitation_expired,
      value.submission_window_elapsed,
      value.requested_at,
      value.expires_at,
      value.submitted_at,
      value.finalized_at,
    ].every((v) => v === null)
  if (
    !generation(value.credential_generation) ||
    value.credential_generation > value.current_credential_generation ||
    typeof value.context_current !== "boolean" ||
    typeof value.invitation_expired !== "boolean" ||
    (value.context_current &&
      value.credential_generation !== value.current_credential_generation) ||
    !timestamp(value.requested_at) ||
    !timestamp(value.expires_at) ||
    Date.parse(value.requested_at) > Date.parse(value.checked_at) ||
    Date.parse(value.expires_at) <= Date.parse(value.requested_at) ||
    value.invitation_expired !==
      Date.parse(value.checked_at) >= Date.parse(value.expires_at)
  )
    return false
  const began = value.submitted_at !== null
  if (
    began &&
    (!timestamp(value.submitted_at) ||
      Date.parse(value.submitted_at) < Date.parse(value.requested_at) ||
      Date.parse(value.submitted_at) > Date.parse(value.checked_at))
  )
    return false
  if (value.status === "queued" || value.status === "submitting")
    return (
      value.outcome_code === null &&
      value.finalized_at === null &&
      (value.status === "queued"
        ? !began && value.submission_window_elapsed === null
        : began &&
          typeof value.submission_window_elapsed === "boolean" &&
          (!value.invitation_expired || value.submission_window_elapsed))
    )
  if (
    value.submission_window_elapsed !== null ||
    !timestamp(value.finalized_at) ||
    Date.parse(value.finalized_at) <
      Date.parse(value.submitted_at ?? value.requested_at) ||
    Date.parse(value.finalized_at) > Date.parse(value.checked_at)
  )
    return false
  if (value.status === "accepted") return began && value.outcome_code === null
  if (value.status === "uncertain")
    return began && value.outcome_code === "submission_uncertain"
  if (value.status === "failed")
    return ["transport_unavailable", "message_invalid"].includes(
      value.outcome_code ?? "",
    )
  return [
    "scope_unavailable",
    "obsolete_context",
    "consent_withdrawn",
    "expired",
  ].includes(value.outcome_code ?? "")
}

export function VisitPrepEmailStatus({
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
  const [value, setValue] = useState<VisitPrepEmailStatusResponse | null>(null)
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
      const result = await visitPrepEmailStatus({
        path: { prep_id: preparationId },
        cache: "no-store",
      })
      if (epoch.current !== current) return
      if (
        result.error ||
        !result.data ||
        !validEmailStatus(result.data, preparationId)
      )
        throw new Error("unavailable")
      setScope(scopeKey)
      setValue(result.data)
    } catch {
      if (epoch.current === current)
        setError(
          "Invitation status is unavailable. Reload to check; do not assume a failed read means the email failed.",
        )
    } finally {
      if (epoch.current === current) {
        pending.current = false
        setBusy(false)
      }
    }
  }
  if (!allowed || blocked)
    return <p>Invitation status is unavailable in this session.</p>
  return (
    <section
      className="min-w-0 space-y-3 rounded-md border p-4"
      aria-labelledby="visit-prep-email-status-title"
    >
      <h3 id="visit-prep-email-status-title" className="font-semibold">
        Invitation email status
      </h3>
      <Button variant="outline" disabled={busy} onClick={() => void load()}>
        Load / reload invitation status
      </Button>
      {busy && <p role="status">Loading invitation status…</p>}
      {error && <p role="alert">{error}</p>}
      {value &&
        scope === scopeKey &&
        value.preparation_id === preparationId && (
          <>
            <p className="font-medium">{labels[value.status]}</p>
            {value.outcome_code && (
              <p className="text-sm">{reasons[value.outcome_code]}</p>
            )}
            <p className="text-sm">
              This is the recorded state at last load, not confirmed recipient
              delivery or permission to send. The email worker remains off until
              separately qualified and enabled; automatic booking and SMS
              reminders are not connected.
            </p>
            {value.status === "accepted" && (
              <p className="text-sm">
                Provider acceptance does not prove the patient received or
                opened the email.
              </p>
            )}
            {["submitting", "uncertain"].includes(value.status) && (
              <p className="text-sm">
                Do not resend: the original attempt may already have reached the
                email provider.
              </p>
            )}
            {value.context_current === false && (
              <p className="text-sm">
                This invitation belongs to an obsolete or inactive preparation
                context.
              </p>
            )}
            {value.invitation_expired && (
              <p className="text-sm">
                The invitation’s sending deadline has passed.
              </p>
            )}
            {value.submission_window_elapsed && (
              <p className="text-sm">
                The submission window has elapsed; final reconciliation is not
                recorded.
              </p>
            )}
            <dl className="text-muted-foreground space-y-1 text-xs break-all">
              <dt>Preparation</dt>
              <dd>{value.preparation_id}</dd>
              <dt>Checked at (UTC)</dt>
              <dd>{new Date(value.checked_at).toISOString()}</dd>
              {value.requested_at && (
                <>
                  <dt>Requested at (UTC)</dt>
                  <dd>{new Date(value.requested_at).toISOString()}</dd>
                </>
              )}
              {value.submitted_at && (
                <>
                  <dt>Submission claimed at (UTC)</dt>
                  <dd>{new Date(value.submitted_at).toISOString()}</dd>
                </>
              )}
              {value.finalized_at && (
                <>
                  <dt>Outcome recorded at (UTC)</dt>
                  <dd>{new Date(value.finalized_at).toISOString()}</dd>
                </>
              )}
            </dl>
          </>
        )}
    </section>
  )
}
