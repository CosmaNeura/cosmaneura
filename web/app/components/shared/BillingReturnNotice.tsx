import { useEffect, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router"

import { Loader2 } from "lucide-react"

import { Button } from "~/components/ui/button"
import { safeBillingReturnTo } from "~/lib/billing-recovery"
import {
  type PracticeStatus,
  getPractice,
  practiceReady,
} from "~/lib/practice-api"

export { safeBillingReturnTo } from "~/lib/billing-recovery"

const POLL_DELAY_MS = 2_500
const MAX_POLL_ATTEMPTS = 24

type BillingReturnState =
  | "idle"
  | "cancelled"
  | "confirming"
  | "confirmed"
  | "delayed"

function stripeSubscriptionConfirmed(practice: PracticeStatus | null) {
  return Boolean(
    practice?.billing_subscription_manageable &&
      practice.entitled &&
      (practice.subscription_status === "active" ||
        practice.subscription_status === "trialing"),
  )
}

function initialState(
  result: string | null,
  practice: PracticeStatus | null,
): BillingReturnState {
  if (result === "cancelled") return "cancelled"
  if (result !== "success") return "idle"
  return stripeSubscriptionConfirmed(practice) ? "confirmed" : "confirming"
}

export function BillingReturnNotice({
  practice,
  onPracticeChange,
}: {
  practice: PracticeStatus | null
  onPracticeChange: (practice: PracticeStatus) => void
}) {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const result = searchParams.get("billing")
  const returnTo = safeBillingReturnTo(searchParams.get("returnTo"))
  const [state, setState] = useState<BillingReturnState>(() =>
    initialState(result, practice),
  )
  const [pollRun, setPollRun] = useState(0)
  const [confirmedPractice, setConfirmedPractice] = useState(practice)
  const attempts = useRef(0)
  const accessReady = Boolean(
    practiceReady(confirmedPractice) &&
      confirmedPractice.entitled &&
      !confirmedPractice.access_ended,
  )

  useEffect(() => {
    if (result === "cancelled") {
      setState("cancelled")
      return
    }
    if (result !== "success") {
      setState("idle")
      return
    }
    if (practice && stripeSubscriptionConfirmed(practice)) {
      setConfirmedPractice(practice)
      onPracticeChange(practice)
      setState("confirmed")
      return
    }

    let cancelled = false
    let timer: number | undefined
    attempts.current = 0
    setState("confirming")

    async function checkPractice() {
      try {
        const refreshed = await getPractice()
        if (cancelled) return
        if (refreshed && stripeSubscriptionConfirmed(refreshed)) {
          setConfirmedPractice(refreshed)
          onPracticeChange(refreshed)
          setState("confirmed")
          return
        }
      } catch {
        // Stripe and the application can settle independently. Treat a
        // transient status request failure like a pending webhook and retry.
      }

      if (cancelled) return
      attempts.current += 1
      if (attempts.current >= MAX_POLL_ATTEMPTS) {
        setState("delayed")
        return
      }
      timer = window.setTimeout(checkPractice, POLL_DELAY_MS)
    }

    timer = window.setTimeout(checkPractice, POLL_DELAY_MS)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [onPracticeChange, pollRun, practice, result])

  if (state === "idle") return null

  return (
    <div
      className="mb-5 w-full rounded-lg border p-4 text-left"
      role="status"
      aria-live="polite"
    >
      {state === "cancelled" ? (
        <>
          <p className="font-semibold">Checkout canceled</p>
          <p className="text-muted-foreground mt-1 text-sm">
            No billing changes were made. You can resume checkout from the
            practice section whenever you are ready.
          </p>
        </>
      ) : state === "confirmed" ? (
        <>
          <p className="font-semibold">Subscription confirmed</p>
          <p className="text-muted-foreground mt-1 text-sm">
            {accessReady
              ? "Stripe confirmed your subscription. Your practice access is ready."
              : "Stripe confirmed your subscription. Review the remaining practice access requirements below."}
          </p>
          {accessReady && (
            <Button
              className="mt-3"
              size="sm"
              onClick={() => navigate(returnTo)}
            >
              Continue to Cosmas
            </Button>
          )}
        </>
      ) : state === "delayed" ? (
        <>
          <p className="font-semibold">
            Subscription confirmation is taking longer
          </p>
          <p className="text-muted-foreground mt-1 text-sm">
            Stripe has not confirmed access yet. Do not pay a second time. You
            can check again here or contact support if this continues.
          </p>
          <Button
            className="mt-3"
            size="sm"
            variant="outline"
            onClick={() => setPollRun((value) => value + 1)}
          >
            Check again
          </Button>
        </>
      ) : (
        <div className="flex items-start gap-3">
          <Loader2 className="text-primary mt-0.5 h-5 w-5 animate-spin" />
          <div>
            <p className="font-semibold">Confirming your subscription</p>
            <p className="text-muted-foreground mt-1 text-sm">
              Checkout finished. We are waiting for Stripe to confirm your
              subscription; this usually takes only a few moments.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
