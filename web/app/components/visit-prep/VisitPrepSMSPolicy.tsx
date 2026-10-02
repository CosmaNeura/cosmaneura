import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type VisitPrepSettingsData,
  type VisitPrepSmsPolicyResponse,
  visitPrepSmsPolicyDecide,
  visitPrepSmsPolicyRead,
} from "~/configuration/client"
import {
  VIEW_AS_HARD_BLOCK_EVENT,
  subscribeToViewAsStateChanges,
} from "~/lib/view-as-binding"

import { validCadence } from "./VisitPrepEmailPolicy"

const labels = {
  not_approved: "No physician SMS approval recorded",
  approved: "Physician SMS policy approved at last load",
  withdrawn: "Physician SMS approval withdrawn",
  expired: "Physician SMS approval expired",
  policy_changed: "SMS policy changed — new approval required",
  sender_changed: "SMS sender changed — new approval required",
  identity_changed: "Physician identity changed — new approval required",
}
const steps = {
  booking_sms: "Booking invitation",
  unopened_48h_sms: "48-hour unopened reminder",
  unfinished_12h_sms: "12-hour unfinished reminder",
}
const hash = /^[0-9a-f]{64}$/
const time = (v: unknown): v is string =>
  typeof v === "string" &&
  /T.*(?:Z|[+-]\d\d:\d\d)$/i.test(v) &&
  Number.isFinite(Date.parse(v))
const closed = (v: unknown, fields: string[]): v is Record<string, unknown> =>
  !!v &&
  typeof v === "object" &&
  !Array.isArray(v) &&
  Object.keys(v).length === fields.length &&
  fields.every((k) => Object.hasOwn(v, k))

export function validSMSPolicy(value: VisitPrepSmsPolicyResponse): boolean {
  if (
    !closed(value, [
      "contract",
      "policy_fingerprint",
      "policy_text",
      "template_version",
      "sender_digest",
      "templates",
      "status",
      "event_sequence",
      "decision_policy_fingerprint",
      "decision_sender_digest",
      "recorded_at",
      "expires_at",
      "checked_at",
      "delivery_status",
      "standard_cadence",
    ]) ||
    value.contract !== "visit_prep.sms_policy.v1" ||
    value.template_version !== "visit_prep_sms_v1" ||
    value.delivery_status !== "not_connected" ||
    !hash.test(value.policy_fingerprint) ||
    typeof value.policy_text !== "string" ||
    value.policy_text.length < 1 ||
    value.policy_text.length > 2000 ||
    (value.sender_digest !== null && !hash.test(value.sender_digest)) ||
    !Object.hasOwn(labels, value.status) ||
    !Number.isInteger(value.event_sequence) ||
    value.event_sequence < 0 ||
    value.event_sequence > 2_147_483_647 ||
    !time(value.checked_at) ||
    !closed(value.templates, Object.keys(steps)) ||
    !Object.values(value.templates).every(
      (t) =>
        typeof t === "string" &&
        t.length <= 2000 &&
        t.includes("{private_preparation_link}") &&
        !t.includes("vp1_") &&
        !/https?:\/\//i.test(t),
    ) ||
    (value.standard_cadence !== null &&
      (!value.standard_cadence || !validCadence(value.standard_cadence)))
  )
    return false
  if (value.event_sequence === 0)
    return (
      value.status === "not_approved" &&
      value.decision_policy_fingerprint === null &&
      value.decision_sender_digest === null &&
      value.recorded_at === null &&
      value.expires_at === null
    )
  if (
    value.status === "not_approved" ||
    !time(value.recorded_at) ||
    Date.parse(value.recorded_at) > Date.parse(value.checked_at) ||
    !hash.test(value.decision_policy_fingerprint ?? "") ||
    !hash.test(value.decision_sender_digest ?? "")
  )
    return false
  if (value.status === "withdrawn") return value.expires_at === null
  if (
    !time(value.expires_at) ||
    Date.parse(value.expires_at) <= Date.parse(value.recorded_at)
  )
    return false
  if (value.status === "approved")
    return (
      value.sender_digest !== null &&
      value.sender_digest === value.decision_sender_digest &&
      value.policy_fingerprint === value.decision_policy_fingerprint &&
      Date.parse(value.expires_at) > Date.parse(value.checked_at)
    )
  if (value.status === "expired")
    return Date.parse(value.expires_at) <= Date.parse(value.checked_at)
  if (value.status === "sender_changed")
    return value.sender_digest !== value.decision_sender_digest
  if (value.status === "policy_changed")
    return value.policy_fingerprint !== value.decision_policy_fingerprint
  return true
}

export function VisitPrepSMSPolicy({
  access,
  scopeKey,
}: {
  access: VisitPrepSettingsData
  scopeKey: string
}) {
  const allowed = access.eligible && access.can_manage && !!scopeKey
  const epoch = useRef(0),
    pending = useRef(false)
  const [scope, setScope] = useState(scopeKey),
    [blocked, setBlocked] = useState(false)
  const [value, setValue] = useState<VisitPrepSmsPolicyResponse | null>(null)
  const [expiry, setExpiry] = useState(""),
    [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState<string | null>(null)
  useEffect(() => {
    function clear() {
      epoch.current++
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
      epoch.current++
      window.removeEventListener("blur", clear)
      window.removeEventListener("pagehide", clear)
      document.removeEventListener("visibilitychange", conceal)
      window.removeEventListener(VIEW_AS_HARD_BLOCK_EVENT, block)
      unsubscribe()
    }
  }, [allowed, scopeKey])
  async function load() {
    if (!allowed || blocked || pending.current) return
    const current = ++epoch.current
    pending.current = true
    setBusy(true)
    setValue(null)
    setExpiry("")
    setReviewed(false)
    setMessage(null)
    try {
      const result = await visitPrepSmsPolicyRead({ cache: "no-store" })
      if (epoch.current !== current) return
      if (result.error || !result.data || !validSMSPolicy(result.data))
        throw new Error("unavailable")
      setScope(scopeKey)
      setValue(result.data)
    } catch {
      if (epoch.current === current)
        setMessage(
          "SMS policy could not be loaded. Reload to check current state.",
        )
    } finally {
      if (epoch.current === current) {
        pending.current = false
        setBusy(false)
      }
    }
  }
  const deadline = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(expiry)
    ? Date.parse(expiry + ":00Z")
    : NaN
  const future =
    value &&
    Number.isFinite(deadline) &&
    deadline > Math.max(Date.now(), Date.parse(value.checked_at))
  async function decide(kind: "approved" | "withdrawn") {
    if (
      !allowed ||
      blocked ||
      pending.current ||
      !value ||
      scope !== scopeKey ||
      value.event_sequence >= 2_147_483_647
    )
      return
    if (kind === "approved" && (!reviewed || !future || !value.sender_digest))
      return
    if (
      kind === "withdrawn" &&
      (!value.event_sequence || value.status === "withdrawn")
    )
      return
    const body = {
      expected_event_sequence: value.event_sequence,
      event_kind: kind,
      policy_fingerprint:
        kind === "withdrawn"
          ? value.decision_policy_fingerprint!
          : value.policy_fingerprint,
      expected_sender_digest:
        kind === "withdrawn"
          ? value.decision_sender_digest!
          : value.sender_digest!,
      expires_at: kind === "approved" ? new Date(deadline).toISOString() : null,
    }
    const current = ++epoch.current
    pending.current = true
    setBusy(true)
    setValue(null)
    setExpiry("")
    setReviewed(false)
    setMessage(null)
    try {
      const result = await visitPrepSmsPolicyDecide({
        body,
        headers: { "Idempotency-Key": crypto.randomUUID() },
        cache: "no-store",
      })
      if (epoch.current !== current) return
      const receipt = result.data
      if (
        result.error ||
        !receipt ||
        !closed(receipt, [
          "event_sequence",
          "current_event_sequence",
          "event_kind",
          "policy_fingerprint",
          "expires_at",
          "recorded_at",
          "disposition",
        ]) ||
        !["recorded", "replayed"].includes(receipt.disposition) ||
        receipt.event_sequence !== body.expected_event_sequence + 1 ||
        !Number.isInteger(receipt.current_event_sequence) ||
        receipt.current_event_sequence < receipt.event_sequence ||
        receipt.current_event_sequence > 2_147_483_647 ||
        receipt.event_kind !== kind ||
        receipt.policy_fingerprint !== body.policy_fingerprint ||
        !time(receipt.recorded_at) ||
        (body.expires_at === null
          ? receipt.expires_at !== null
          : !time(receipt.expires_at) ||
            Date.parse(receipt.expires_at) !== deadline)
      )
        throw new Error("unavailable")
      setMessage(
        "Decision recorded. Reload the policy to check current state. No text was sent.",
      )
    } catch {
      if (epoch.current === current)
        setMessage(
          "The decision outcome is unknown. Reload the policy before another decision; do not assume it failed.",
        )
    } finally {
      if (epoch.current === current) {
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
      aria-labelledby="visit-prep-sms-policy-title"
    >
      <h2 id="visit-prep-sms-policy-title" className="text-xl font-semibold">
        Visit Prep SMS policy
      </h2>
      <p className="text-sm">
        Review the fixed texts and record physician approval or withdrawal. This
        does not create patient consent or enable sending.
      </p>
      <p className="rounded-md border p-3 text-sm">
        SMS delivery is not connected. No text is sent by this control.
      </p>
      <Button type="button" disabled={busy} onClick={() => void load()}>
        {busy ? "Working…" : "Load / reload SMS policy"}
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
            Snapshot checked at {shown.checked_at}. Reload to check for changes.
          </p>
          <p className="text-sm">{shown.policy_text}</p>
          <div className="rounded-md border p-3 text-sm">
            <h3 className="font-medium">Standard SMS reminder timing</h3>
            <p>Managed by Cosmaneura. Providers cannot edit these settings.</p>
            {shown.standard_cadence ? (
              <p>
                No messages from{" "}
                {String(shown.standard_cadence.quiet_start_hour).padStart(
                  2,
                  "0",
                )}
                :00 to{" "}
                {String(shown.standard_cadence.quiet_end_hour).padStart(2, "0")}
                :00 in the current appointment’s timezone. At least{" "}
                {shown.standard_cadence.minimum_channel_spacing_hours} hours
                between messages on the same channel.
              </p>
            ) : (
              <p>
                Standard timing is unavailable. Automated sending remains
                disabled.
              </p>
            )}
          </div>
          {Object.entries(steps).map(([step, label]) => (
            <div key={step} className="min-w-0 rounded-md border p-3">
              <h3 className="font-medium">{label} preview</h3>
              <p className="mt-2 text-sm [overflow-wrap:anywhere] whitespace-pre-wrap">
                {shown.templates[step as keyof typeof steps]}
              </p>
            </div>
          ))}
          <p className="text-muted-foreground text-sm">
            The private-link placeholder is not a working patient invitation.{" "}
            {shown.sender_digest
              ? "Sender routing is configured, not qualified for delivery."
              : "No SMS sender is configured; new approval is unavailable."}
          </p>
          <label className="block space-y-1 text-sm">
            <span>SMS approval expires at (UTC)</span>
            <input
              type="datetime-local"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
              className="block max-w-full rounded-md border p-2"
            />
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(e) => setReviewed(e.target.checked)}
            />
            <span>
              I reviewed these exact texts and the configured sender and approve
              the SMS policy until the stated expiry.
            </span>
          </label>
          <div className="flex flex-wrap gap-3">
            <Button
              type="button"
              disabled={
                !shown.sender_digest ||
                !reviewed ||
                !future ||
                shown.event_sequence >= 2_147_483_647
              }
              onClick={() => void decide("approved")}
            >
              Record physician SMS approval
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={
                !shown.event_sequence ||
                shown.status === "withdrawn" ||
                shown.event_sequence >= 2_147_483_647
              }
              onClick={() => void decide("withdrawn")}
            >
              Withdraw physician SMS approval
            </Button>
          </div>
          <details className="text-muted-foreground text-xs">
            <summary>SMS policy and sender version</summary>
            <dl className="mt-2 space-y-1 break-all">
              <dt>Current policy</dt>
              <dd>{shown.policy_fingerprint}</dd>
              <dt>Current sender binding</dt>
              <dd>{shown.sender_digest ?? "Not configured"}</dd>
              <dt>Decision sequence</dt>
              <dd>{shown.event_sequence}</dd>
              <dt>Recorded approval expiry</dt>
              <dd>{shown.expires_at ?? "None"}</dd>
            </dl>
          </details>
        </>
      )}
    </section>
  )
}
