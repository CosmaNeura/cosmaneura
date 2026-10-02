import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type VisitPrepCommandStateResponse,
  type VisitPrepEmailInvitationResponse,
  type VisitPrepSettingsData,
  visitPrepEmailInvitation,
} from "~/configuration/client"
import {
  VIEW_AS_HARD_BLOCK_EVENT,
  subscribeToViewAsStateChanges,
} from "~/lib/view-as-binding"

import { VisitPrepEmailStatus } from "./VisitPrepEmailStatus"

export function validInvitationReceipt(
  value: VisitPrepEmailInvitationResponse,
  id: string,
  generation: number,
): boolean {
  const fields = [
    "contract",
    "preparation_id",
    "credential_generation",
    "state",
    "disposition",
    "requested_at",
    "expires_at",
    "dispatch_integration",
  ]
  return Boolean(
    value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      Object.keys(value).length === fields.length &&
      fields.every((key) => Object.hasOwn(value, key)) &&
      value.contract === "visit_prep.email_invitation.v1" &&
      value.preparation_id === id &&
      value.credential_generation === generation &&
      ["queued", "replayed"].includes(value.disposition) &&
      [
        "queued",
        "submitting",
        "accepted",
        "failed",
        "suppressed",
        "uncertain",
      ].includes(value.state) &&
      value.dispatch_integration === "default_off_worker" &&
      typeof value.requested_at === "string" &&
      typeof value.expires_at === "string" &&
      Number.isFinite(Date.parse(value.requested_at)) &&
      Number.isFinite(Date.parse(value.expires_at)) &&
      Date.parse(value.expires_at) > Date.parse(value.requested_at),
  )
}

export function VisitPrepEmailInvitation({
  preparation,
  access,
  scopeKey,
  getAccessUrl,
  onQueued,
}: {
  preparation: VisitPrepCommandStateResponse
  access: VisitPrepSettingsData
  scopeKey: string
  getAccessUrl: () => string | null
  onQueued: () => void
}) {
  const allowed =
    access.eligible &&
    access.can_manage &&
    access.can_send_email &&
    Boolean(scopeKey)
  const epoch = useRef(0)
  const intent = useRef<{
    key: string
    token: string
    generation: number
  } | null>(null)
  const pending = useRef(false)
  const attempted = useRef(false)
  const [busy, setBusy] = useState(false)
  const [blocked, setBlocked] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [receipt, setReceipt] =
    useState<VisitPrepEmailInvitationResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    function conceal() {
      const wasAttempted = attempted.current
      epoch.current += 1
      intent.current = null
      pending.current = false
      setBusy(false)
      setReceipt(null)
      // Once a request may have reached Core, never make concealment a new-key retry.
      setUncertain((current) => current || wasAttempted)
    }
    function hardBlock() {
      conceal()
      setBlocked(true)
    }
    function hide() {
      if (document.visibilityState === "hidden") conceal()
    }
    epoch.current += 1
    intent.current = null
    pending.current = false
    setReceipt(null)
    setError(null)
    setBusy(false)
    setUncertain(false)
    attempted.current = false
    window.addEventListener("blur", conceal)
    window.addEventListener("pagehide", conceal)
    document.addEventListener("visibilitychange", hide)
    window.addEventListener(VIEW_AS_HARD_BLOCK_EVENT, hardBlock)
    const unsubscribe = subscribeToViewAsStateChanges(hardBlock)
    return () => {
      epoch.current += 1
      intent.current = null
      window.removeEventListener("blur", conceal)
      window.removeEventListener("pagehide", conceal)
      document.removeEventListener("visibilitychange", hide)
      window.removeEventListener(VIEW_AS_HARD_BLOCK_EVENT, hardBlock)
      unsubscribe()
    }
  }, [scopeKey, preparation.id, preparation.credential_generation, allowed])

  async function queue() {
    if (
      !allowed ||
      blocked ||
      pending.current ||
      receipt ||
      (uncertain && !intent.current)
    )
      return
    if (!intent.current) {
      const link = getAccessUrl()
      if (!link) {
        setError(
          "The private link is no longer held in this tab. Check invitation status.",
        )
        return
      }
      const token = new URL(link).hash.slice("#access_token=".length)
      if (!/^vp1_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/u.test(token)) return
      intent.current = {
        key: crypto.randomUUID(),
        token,
        generation: preparation.credential_generation,
      }
    }
    const request = intent.current
    const current = ++epoch.current
    pending.current = true
    attempted.current = true
    setBusy(true)
    setError(null)
    try {
      const result = await visitPrepEmailInvitation({
        path: { prep_id: preparation.id },
        body: {
          expected_credential_generation: request.generation,
          access_token: request.token,
        },
        headers: { "Idempotency-Key": request.key },
        cache: "no-store",
      })
      if (epoch.current !== current) return
      const value = result.data
      if (
        result.error ||
        !value ||
        !validInvitationReceipt(value, preparation.id, request.generation)
      )
        throw new Error("unavailable")
      setReceipt(value)
      intent.current = null
      onQueued()
    } catch {
      if (epoch.current === current) {
        setUncertain(true)
        setError(
          "Handoff was not acknowledged. Check status first; retry only this same request, never create another invitation to recover it.",
        )
      }
    } finally {
      if (epoch.current === current) {
        pending.current = false
        setBusy(false)
      }
    }
  }

  if (!allowed || blocked) return null
  return (
    <section
      className="min-w-0 space-y-3 border-t pt-4"
      data-sentry-block=""
      data-ph-no-capture=""
    >
      <h3 className="font-medium">Email invitation</h3>
      <p className="text-muted-foreground text-sm">
        Queue the existing private link for the recorded recipient. Core
        requires current physician approval and qualified, explicit email
        permission. Quiet hours and spacing are centrally managed. This does not
        send SMS.
      </p>
      <Button
        type="button"
        variant="outline"
        disabled={busy || Boolean(receipt) || (uncertain && !intent.current)}
        onClick={() => void queue()}
      >
        {busy
          ? "Queuing invitation…"
          : uncertain
            ? "Retry same invitation handoff"
            : "Queue email invitation"}
      </Button>
      {error && (
        <p role="alert" className="text-sm">
          {error}
        </p>
      )}
      {receipt && (
        <p role="status" className="text-sm">
          {receipt.disposition === "replayed"
            ? "Recovered the original invitation record"
            : "Invitation queued"}{" "}
          ({receipt.state}). This is not confirmed recipient delivery.
        </p>
      )}
      <VisitPrepEmailStatus
        preparationId={preparation.id}
        access={access}
        scopeKey={scopeKey}
      />
    </section>
  )
}
