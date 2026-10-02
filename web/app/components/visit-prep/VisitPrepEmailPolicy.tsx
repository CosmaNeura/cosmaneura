import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type VisitPrepEmailPolicyResponse,
  type VisitPrepSettingsData,
  visitPrepEmailPolicyDecide,
  visitPrepEmailPolicyRead,
} from "~/configuration/client"
import {
  VIEW_AS_HARD_BLOCK_EVENT,
  subscribeToViewAsStateChanges,
} from "~/lib/view-as-binding"

const labels = {
  not_approved: "No physician approval recorded",
  approved: "Physician policy approved at last load",
  withdrawn: "Physician approval withdrawn",
  expired: "Physician approval expired",
  policy_changed: "Invitation policy changed — new approval required",
}
const hash = /^[0-9a-f]{64}$/
function valid(value: VisitPrepEmailPolicyResponse): boolean {
  return (
    value.contract === "visit_prep.email_policy.v1" &&
    value.delivery_status === "not_connected" &&
    value.template_version === "visit_prep_invitation_v1" &&
    typeof value.subject === "string" &&
    value.subject.length <= 200 &&
    typeof value.template_text === "string" &&
    value.template_text.length <= 8000 &&
    value.template_text.includes("{private_preparation_link}") &&
    hash.test(value.policy_fingerprint) &&
    Object.hasOwn(labels, value.status) &&
    Number.isInteger(value.event_sequence) &&
    value.event_sequence >= 0 &&
    value.event_sequence <= 2_147_483_647 &&
    Number.isFinite(Date.parse(value.checked_at)) &&
    (value.event_sequence === 0
      ? value.status === "not_approved" &&
        value.decision_policy_fingerprint === null
      : typeof value.decision_policy_fingerprint === "string" &&
        hash.test(value.decision_policy_fingerprint))
  )
}

export function VisitPrepEmailPolicy({
  access,
  scopeKey,
}: {
  access: VisitPrepSettingsData
  scopeKey: string
}) {
  const allowed = access.eligible && access.can_manage && Boolean(scopeKey)
  const generation = useRef(0)
  const pending = useRef(false)
  const [scope, setScope] = useState(scopeKey)
  const [blocked, setBlocked] = useState(false)
  const [value, setValue] = useState<VisitPrepEmailPolicyResponse | null>(null)
  const [expiry, setExpiry] = useState("")
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    function clear() {
      generation.current += 1
      pending.current = false
      setScope(scopeKey)
      setValue(null)
      setExpiry("")
      setReviewed(false)
      setBusy(false)
      setMessage(null)
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
      generation.current += 1
      window.removeEventListener("blur", clear)
      window.removeEventListener("pagehide", clear)
      document.removeEventListener("visibilitychange", conceal)
      window.removeEventListener(VIEW_AS_HARD_BLOCK_EVENT, block)
      unsubscribe()
    }
  }, [allowed, scopeKey])

  async function load() {
    if (!allowed || blocked || pending.current) return
    const current = ++generation.current
    pending.current = true
    setBusy(true)
    setValue(null)
    setExpiry("")
    setReviewed(false)
    setMessage(null)
    try {
      const result = await visitPrepEmailPolicyRead({ cache: "no-store" })
      if (generation.current !== current) return
      if (result.error || !result.data || !valid(result.data))
        throw new Error("unavailable")
      setScope(scopeKey)
      setValue(result.data)
    } catch {
      if (generation.current === current)
        setMessage(
          "Email policy could not be loaded. Reload to check current state.",
        )
    } finally {
      if (generation.current === current) {
        pending.current = false
        setBusy(false)
      }
    }
  }

  const deadline = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(expiry)
    ? Date.parse(expiry + ":00Z")
    : NaN
  const future =
    value !== null &&
    Number.isFinite(deadline) &&
    deadline > Math.max(Date.now(), Date.parse(value.checked_at))
  async function decide(kind: "approved" | "withdrawn") {
    if (!allowed || blocked || pending.current || !value || scope !== scopeKey)
      return
    if (kind === "approved" && (!reviewed || !future)) return
    if (
      kind === "withdrawn" &&
      (value.event_sequence === 0 || value.status === "withdrawn")
    )
      return
    const body = {
      expected_event_sequence: value.event_sequence,
      event_kind: kind,
      policy_fingerprint:
        kind === "withdrawn"
          ? value.decision_policy_fingerprint!
          : value.policy_fingerprint,
      expires_at: kind === "approved" ? new Date(deadline).toISOString() : null,
    }
    const current = ++generation.current
    pending.current = true
    setBusy(true)
    setValue(null)
    setReviewed(false)
    setExpiry("")
    setMessage(null)
    try {
      const key = crypto.randomUUID()
      const result = await visitPrepEmailPolicyDecide({
        body,
        headers: { "Idempotency-Key": key },
        cache: "no-store",
      })
      if (generation.current !== current) return
      const receipt = result.data
      if (
        result.error ||
        !receipt ||
        !["recorded", "replayed"].includes(receipt.disposition) ||
        receipt.event_sequence !== body.expected_event_sequence + 1 ||
        !Number.isInteger(receipt.current_event_sequence) ||
        receipt.current_event_sequence < receipt.event_sequence ||
        receipt.event_kind !== body.event_kind ||
        receipt.policy_fingerprint !== body.policy_fingerprint ||
        !Number.isFinite(Date.parse(receipt.recorded_at)) ||
        (body.expires_at === null
          ? receipt.expires_at !== null
          : receipt.expires_at === null ||
            Date.parse(receipt.expires_at) !== deadline)
      )
        throw new Error("unavailable")
      // A receipt is historical, never current approval or permission to send.
      setMessage(
        "Decision recorded. Reload the policy to check current state. No invitation was sent.",
      )
    } catch {
      if (generation.current === current)
        setMessage(
          "The decision outcome is unknown. Reload the policy before another decision; do not assume it failed.",
        )
    } finally {
      if (generation.current === current) {
        pending.current = false
        setBusy(false)
      }
    }
  }

  if (!allowed || blocked) return null
  const shown = scope === scopeKey ? value : null
  return (
    <section
      className="mx-auto my-6 max-w-3xl space-y-4 rounded-md border p-4"
      aria-labelledby="visit-prep-email-policy-title"
    >
      <h2 id="visit-prep-email-policy-title" className="text-xl font-semibold">
        Visit Prep invitation email policy
      </h2>
      <p className="text-sm">
        Review the fixed invitation and record physician approval or withdrawal.
        This does not create patient consent or enable sending.
      </p>
      <p className="rounded-md border p-3 text-sm">
        Delivery is not connected. No email or SMS is sent by this control.
      </p>
      <Button type="button" disabled={busy} onClick={() => void load()}>
        {busy ? "Working…" : "Load / reload email policy"}
      </Button>
      {message && scope === scopeKey && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
      {shown && (
        <>
          <p className="font-medium">{labels[shown.status]}</p>
          <p className="text-muted-foreground text-sm">
            Snapshot checked at {shown.checked_at}. Reload to check for changes.{" "}
            {shown.expires_at && (
              <>Recorded approval expiry: {shown.expires_at}.</>
            )}
          </p>
          <div className="min-w-0 rounded-md border p-4">
            <h3 className="font-medium">Invitation preview</h3>
            <p className="mt-2 break-words">Subject: {shown.subject}</p>
            <p className="mt-3 [overflow-wrap:anywhere] whitespace-pre-wrap">
              {shown.template_text}
            </p>
            <p className="text-muted-foreground mt-3 text-sm">
              The private-link placeholder is not a working patient invitation.
            </p>
          </div>
          <label className="block space-y-1 text-sm">
            <span>Approval expires at (UTC)</span>
            <input
              type="datetime-local"
              value={expiry}
              onChange={(event) => setExpiry(event.target.value)}
              className="block max-w-full rounded-md border p-2"
            />
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(event) => setReviewed(event.target.checked)}
            />
            <span>
              I reviewed this exact invitation and approve its email policy
              until the stated expiry.
            </span>
          </label>
          <div className="flex flex-wrap gap-3">
            <Button
              type="button"
              disabled={
                !reviewed || !future || shown.event_sequence >= 2_147_483_647
              }
              onClick={() => void decide("approved")}
            >
              Record physician approval
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={
                shown.event_sequence === 0 ||
                shown.status === "withdrawn" ||
                shown.event_sequence >= 2_147_483_647
              }
              onClick={() => void decide("withdrawn")}
            >
              Withdraw physician approval
            </Button>
          </div>
          <details className="text-muted-foreground text-xs">
            <summary>Policy version and decision sequence</summary>
            <dl className="mt-2 space-y-1 break-all">
              <dt>Current policy fingerprint</dt>
              <dd>{shown.policy_fingerprint}</dd>
              <dt>Decision sequence</dt>
              <dd>{shown.event_sequence}</dd>
              <dt>Previously decided policy</dt>
              <dd>{shown.decision_policy_fingerprint ?? "No decision"}</dd>
            </dl>
          </details>
        </>
      )}
    </section>
  )
}
